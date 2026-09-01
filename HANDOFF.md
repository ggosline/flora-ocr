# Flore du Gabon OCR — RunPod Handoff

Working notes for continuing the scanned-volume OCR batch from any machine.
Last updated: 2026-07-17.

> The SSH **private key** is deliberately NOT in this file (or the repo). Copy it
> to the new machine separately — see "Reconnect" below.

## The work is machine-independent

The OCR runs under `nohup` on the RunPod pod and writes to a **persistent
network volume**. Nothing depends on any particular laptop: you can close a
machine and the batch keeps running; output survives until the volume is
explicitly deleted. "Continuing elsewhere" just means re-establishing access.

## Pod & volume

**Pod is TERMINATED as of 2026-07-17** (billing stopped). Everything lives on the
persistent network volume below. To resume, provision a NEW pod attached to it.

| Item | Value |
|---|---|
| Network volume | `iqhg1urz3t` (name `modern_brown_lemming`), **60 GB**, DC **EU-RO-1** |
| Volume mount (when attached) | `/workspace` |
| Repo on volume | `/workspace/flora-ocr` |
| Output on volume | `/workspace/flora-ocr/ocr_output/` |
| OCR venv on volume | `/workspace/venv-ocr` (paddle + PaddleOCR-VL, native backend — ready to reuse) |
| PDFs on volume | `/workspace/flora-ocr/floras/flore_du_gabon/FdG vol. N OK.pdf` (vols 1-12 present) |
| SSH key | `id_ed25519_herbarium` (private key on the origin machine's `~/.ssh/`; registered on the RunPod account) |

To resume: provision a GPU pod (RTX 4090/A5000, CC ≥ 8.0) **in EU-RO-1** attached
to volume `iqhg1urz3t` at `/workspace`, base image
`runpod/pytorch:*-cu1290-torch290-ubuntu2204`. The `venv-ocr` and model caches are
already on the volume, so it's ready to OCR immediately — no reinstall. Use the
`herbarium-pipeline` RunPodClient (see below) or the RunPod web console. Note:
resuming requires GPU stock in EU-RO-1 (the volume's DC).

## Reconnect (once a new pod is attached)

**Easiest (no local setup):** log into runpod.io → pod → Connect → **web
terminal** or **Jupyter**. `tail -f /workspace/nohup_batch_2_11.out`.

**SSH:** copy the `id_ed25519_herbarium` private key to the new machine's
`~/.ssh/`, then `ssh -i ~/.ssh/id_ed25519_herbarium root@<ip> -p <port>`.
Or append a new machine's public key to `/root/.ssh/authorized_keys` on the pod
from an existing session.

**Scripted pod control** (resize/terminate/create) uses the sibling repo
`herbarium-pipeline` (`cloud/runpod_client.py`) + a RunPod API key in the OS
keyring (`keyring set herbarium-cloud runpod`). Run its scripts with
`herbarium-pipeline/.venv/Scripts/python.exe` (needs httpx, paramiko, keyring).
Not required just to monitor/pull — the console/SSH suffice.

## How to run OCR (native backend)

**Use the native backend — the vLLM "fast" server is broken (see below).**

```bash
# On the pod, from /workspace:
VL_BACKEND=native nohup ./runpod_setup.sh <vol> [<vol> ...] \
    > /workspace/nohup_batch.out 2>&1 &
```

`runpod_setup.sh` downloads each PDF from Zenodo, then OCRs with `--resume`
(checkpoints under `ocr_output/_paddle_cache/` on the volume, so a killed run
resumes for free). Native runs ~5-13 s/page on the 4090; a volume takes
~7-25 min depending on length.

### Zenodo filename-padding gotcha (IMPORTANT)

Low volumes are **zero-padded** on Zenodo (`FdG vol. 02 OK.pdf` … `09`), but
`runpod_setup.sh` looks for the **unpadded** `FdG vol. N OK.pdf` and its download
loop is not fault-tolerant, so a batch **dies on the first vol 2-9**. Workaround:
pre-download those PDFs to the unpadded names the script expects, then it SKIPs
the download. Zenodo record IDs:

```
2=11002006 3=11002291 4=11002335 5=11002365 6=11004467
7=11004506 8=11004527 9=11004851 10=11004874 11=11005077
12=11005131 (unpadded, downloads fine)
```
(Full 61-record list is in `scripts/runpod_setup.sh` / `floras/flore_du_gabon/download.sh`.)

TODO in repo: make `fetch_volume` try padded+unpadded names and the download
loop `|| true` per volume; default `VL_BACKEND=native`; drop the stale
"~1 min/page" comment.

## Batch progress (2026-07-17)

Scanned volumes = **1-37** (paddle/native). Born-digital 38-60 use liteparse.

- **Done & pulled local:** vols **1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12** (+ pre-existing
  16, 17, 18, 19, 29, and some in 57). Batch `runpod_setup.sh 2 … 11` completed
  cleanly; all output pulled to the repo's `ocr_output/`.
- **Remaining scanned:** vols **13, 14, 15, 20, 21, 22, 23, 24, 25, 26, 27, 28,
  30, 31, 32, 33, 34, 35, 36, 37** (~20 volumes, ~5-8 h native, ~$4-6 GPU).

### Known quality issue — vols 5 & 8 didn't split into families
Their family-heading detection failed, so they finalized into un-prefixed dirs
**`vol5_paddle`** (387K chars) and **`vol8_paddle`** (330K chars) instead of
`Family_volN_paddle`. OCR text is complete; they just need reclassification /
family-splitting before use in the key app. NB: any pull script keyed on the
`*_volN_paddle` (family-prefixed) pattern will SKIP these two — match `vol5_paddle`
/ `vol8_paddle` explicitly.

## Pulling results to a local machine

Output dirs are `ocr_output/{Family}_vol{N}_paddle/` (`text.md`, `text_keyfmt.md`,
`figures.md`, `metadata.json`, `figures/*.png`). Each is "complete" once it has
`metadata.json`. Pull via `rsync`/`scp`/`sftp` from
`/workspace/flora-ocr/ocr_output/`, or leave on the volume and pull later.
(A size-skipping SFTP pull loop was used this session.)

## The vLLM fast backend is BROKEN — do not retry

The `paddleocr genai_server` vLLM path crashes on inference with
`KeyError('pixel_values')` (engine dies → every later page 500s → poisoned
checkpoint). Confirmed it is NOT fixable on the current release set:
- Versions are the intended ones (vllm 0.10.2, transformers 4.57.6, torch 2.8+cu128).
- The documented image-processor fix (PaddleOCR issue #17378 / HF discussion #28)
  is already present.
- Matching client/server model version (1.6) does not help; the official
  `paddleocr doc_parser` client crashes it too.
- `paddleocr` 3.7.0 is already latest on PyPI — no forward fix.
See PaddleOCR issues #17744, #17378 and vllm-project/vllm#29587. The `venv-vllm`
was deleted to reclaim 18 GB; native is the supported path here.

## Cost / cleanup

- Pod bills ~$0.69/hr while RUNNING — **stop or terminate it when idle.**
- Network volume `iqhg1urz3t` bills ~$0.07/GB/mo (~$4.20/mo for 60 GB) until
  deleted. **Download everything, then delete the volume** to stop storage
  charges. Terminating the pod does NOT delete the volume.
