"""Streamlit interactive dichotomous key app.

Loads pre-built *.keys.json files produced by build_key_data.py.

Run:
  conda run -n p12 streamlit run key_app.py
  conda run -n p12 streamlit run key_app.py -- --data vol11.keys.json
"""

import difflib
import json
import os
import re
import signal
import sys
import pathlib
import streamlit as st

# ---------------------------------------------------------------------------
# Data discovery
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
FLORAS_DIR = REPO_ROOT / "floras"


def discover_datasets() -> dict[str, pathlib.Path]:
    """Return {display_title: path} for all floras/*/*.keys.json."""
    found = {}
    for p in sorted(FLORAS_DIR.glob("*/*.keys.json")):
        try:
            meta = json.loads(p.read_text(encoding='utf-8')).get('meta', {})
            title = meta.get('title') or p.stem
        except Exception:
            title = p.stem
        found[title] = p
    return found


@st.cache_data
def load_dataset(path_str: str, _mtime: float) -> dict:
    """Load and return a keys.json dataset. Cached per path+mtime so stale data is never served."""
    data = json.loads(pathlib.Path(path_str).read_text(encoding='utf-8'))
    # Convert figure keys from "genus:epithet" strings to (genus, epithet) tuples.
    # Values may be {"path": ..., "caption": ...} dicts or bare path strings (legacy).
    def _normalise(v):
        if isinstance(v, dict):
            return v
        return {'path': v, 'caption': ''}

    data['_figures_tuple'] = {
        tuple(k.split(':', 1)): _normalise(v)
        for k, v in data.get('figures', {}).items()
    }
    return data


# ---------------------------------------------------------------------------
# Figure lookup
# ---------------------------------------------------------------------------

def _find_figure(key_genus: str | None, terminal_name: str, figures_tuple: dict) -> dict | None:
    """Return {"path": ..., "caption": ...} for the terminal name, or None."""
    if not figures_tuple or not terminal_name:
        return None
    # Strip leading numbering "3. " or Roman-numeral prefix "I. "
    name = re.sub(r'^(?:\d+|[IVX]+)[\'"]?\.\s+', '', terminal_name).strip()
    parts = name.split()
    if len(parts) < 2:
        return None  # bare genus name — no species figure

    epithet = parts[-1].rstrip('.').lower()
    genus   = (key_genus or '').lower()
    all_epithets = list({e for (g, e) in figures_tuple})

    def _lookup(ep: str) -> dict | None:
        if genus:
            fig = figures_tuple.get((genus, ep))
            if fig:
                return fig
        for (g, e), fig in figures_tuple.items():
            if e == ep:
                return fig
        return None

    fig = _lookup(epithet)
    if fig:
        return fig
    # Fuzzy match for OCR spelling variants (e.g. staudti/staudtii)
    close = difflib.get_close_matches(epithet, all_epithets, n=1, cutoff=0.88)
    if close:
        return _lookup(close[0])
    return None


# ---------------------------------------------------------------------------
# Session-state helpers
# ---------------------------------------------------------------------------

def _default_key_id(data: dict) -> str:
    explicit = data.get('default_key', '')
    if explicit and explicit in data['keys']:
        return explicit
    return next(iter(data['keys']))


def _init(data: dict):
    kid = _default_key_id(data)
    if 'key_id' not in st.session_state or st.session_state.get('_dataset') != data['meta']['title']:
        st.session_state._dataset    = data['meta']['title']
        st.session_state.key_id      = kid
        st.session_state.couplet_num = data['keys'][kid]['start']
        st.session_state.history     = []
        st.session_state.phase       = 'navigate'
        st.session_state.result_name = ''
        st.session_state.next_key_id = None


def _go_back():
    if not st.session_state.history:
        return
    prev_key_id, prev_couplet, _ = st.session_state.history.pop()
    st.session_state.key_id      = prev_key_id
    st.session_state.couplet_num = prev_couplet
    st.session_state.phase       = 'navigate'


def _restart(data: dict):
    kid = _default_key_id(data)
    st.session_state.key_id      = kid
    st.session_state.couplet_num = data['keys'][kid]['start']
    st.session_state.history     = []
    st.session_state.phase       = 'navigate'
    st.session_state.result_name = ''
    st.session_state.next_key_id = None


def _choose(choice_prime: str, node: dict, genus_links: dict):
    st.session_state.history.append((
        st.session_state.key_id,
        st.session_state.couplet_num,
        choice_prime,
    ))
    if node['is_terminal']:
        st.session_state.result_name = node['terminal_name'] or node['text']
        st.session_state.next_key_id = genus_links.get(st.session_state.result_name)
        st.session_state.phase       = 'result'
    else:
        st.session_state.couplet_num = node['leads_to']
        st.session_state.phase       = 'navigate'


# ---------------------------------------------------------------------------
# UI components
# ---------------------------------------------------------------------------

def _breadcrumb():
    if not st.session_state.history:
        return
    parts = [f"{cnum}{prime}" for _, cnum, prime in st.session_state.history]
    st.caption("Path taken: " + " → ".join(parts))


def _couplet_alternatives(nodes: dict, cnum: int) -> list[dict]:
    """Return all alternatives for couplet number cnum, sorted by prime length."""
    alts = [n for n in nodes.values() if n['num'] == cnum]
    alts.sort(key=lambda n: (len(n['prime']), n['prime']))
    return alts


def _nav_page(data: dict):
    keys         = data['keys']
    figures      = data['_figures_tuple']
    genus_links  = data.get('genus_links', {})
    key          = keys[st.session_state.key_id]
    cnum         = st.session_state.couplet_num
    key_genus    = key.get('genus')

    alts = _couplet_alternatives(key['nodes'], cnum)
    if len(alts) < 2:
        st.error(f"Couplet {cnum} not found in '{key['display']}'.")
        st.button("🔄 Start over", on_click=_restart, args=(data,))
        return

    st.subheader(key['display'])
    _breadcrumb()
    st.markdown("---")

    cols = st.columns(len(alts), gap="large")
    for i, (col, node) in enumerate(zip(cols, alts)):
        prime = node['prime']
        label = f"**{cnum}{prime}.** {node['text']}"
        if node['is_terminal']:
            label += f"  \n✅ *{node['terminal_name']}*"
        with col:
            st.button(label, key=f"btn_{i}", use_container_width=True,
                      on_click=_choose, args=(prime, node, genus_links))
            if node['is_terminal']:
                fig = _find_figure(key_genus, node['terminal_name'], figures)
                if fig:
                    st.image(fig['path'], use_container_width=True)
                    if fig['caption']:
                        st.caption(fig['caption'])

    st.markdown("---")
    col_back, col_restart = st.columns([1, 3])
    with col_back:
        st.button("← Back", on_click=_go_back, disabled=not st.session_state.history)
    with col_restart:
        st.button("🔄 Start over", on_click=_restart, args=(data,))


def _result_page(data: dict):
    keys        = data['keys']
    figures     = data['_figures_tuple']
    name        = st.session_state.result_name
    next_kid    = st.session_state.next_key_id
    key_genus   = keys[st.session_state.key_id].get('genus')

    st.subheader("Result")
    _breadcrumb()
    st.markdown("---")
    st.markdown(f"## ✅ {name}")

    fig = _find_figure(key_genus, name, figures)
    if fig:
        st.image(fig['path'], use_container_width=True)
        if fig['caption']:
            st.caption(fig['caption'])

    if next_kid and next_kid in keys:
        next_key = keys[next_kid]
        st.markdown("This genus has a species key available.")
        if st.button(f"→ Identify to species ({next_key['display']})", type="primary"):
            st.session_state.key_id      = next_kid
            st.session_state.couplet_num = next_key['start']
            st.session_state.phase       = 'navigate'
            st.rerun()
    else:
        st.info("No species key available for this taxon in this dataset.")

    st.markdown("---")
    col_back, col_restart = st.columns([1, 3])
    with col_back:
        st.button("← Back", on_click=_go_back)
    with col_restart:
        st.button("🔄 Start over", on_click=_restart, args=(data,))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # Allow --data override via streamlit's script args
    data_arg = None
    args = sys.argv[1:]
    if '--data' in args:
        data_arg = args[args.index('--data') + 1]

    st.set_page_config(page_title="Botanical Key", page_icon="🌿", layout="centered")

    datasets = discover_datasets()
    if not datasets and not data_arg:
        st.error("No *.keys.json files found. Run build_key_data.py first.")
        return

    with st.sidebar:
        st.markdown("## 🌿 Botanical Key")

        # Family selector
        if data_arg:
            chosen_path = pathlib.Path(data_arg)
            st.markdown(f"**Family:** {chosen_path.stem}")
        elif len(datasets) == 1:
            chosen_path = next(iter(datasets.values()))
            st.markdown(f"**Family:** {next(iter(datasets))}")
        else:
            chosen_title = st.selectbox("Family", list(datasets.keys()))
            chosen_path  = datasets[chosen_title]

        data = load_dataset(str(chosen_path), chosen_path.stat().st_mtime)
        _init(data)

        # Key selector
        st.markdown("### Jump to key")
        keys = data['keys']
        key_options = {v['display']: k for k, v in keys.items()}
        current_display = keys[st.session_state.key_id]['display']
        chosen_display  = st.selectbox("Select key", list(key_options.keys()),
                                       index=list(key_options.keys()).index(current_display))
        chosen_kid = key_options[chosen_display]
        if chosen_kid != st.session_state.key_id:
            st.session_state.key_id      = chosen_kid
            st.session_state.couplet_num = keys[chosen_kid]['start']
            st.session_state.history     = []
            st.session_state.phase       = 'navigate'
            st.rerun()

        st.markdown("---")
        st.markdown("**How to use:** Click the statement that best matches "
                    "your specimen at each step. Use ← Back to revise a choice.")
        st.markdown("---")
        if st.button("⏹ Quit app"):
            os.kill(os.getpid(), signal.SIGTERM)

    title = data['meta'].get('title', 'Botanical Key')
    st.title(f"🌿 {title}")

    if st.session_state.phase == 'navigate':
        _nav_page(data)
    else:
        _result_page(data)


if __name__ == "__main__":
    main()
