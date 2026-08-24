"""Luma: evidence-first environmental intelligence for consumer electronics."""
from __future__ import annotations

import html
import json
import re
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

import altair as alt
import joblib
import pandas as pd
import streamlit as st

from src.device_search import (
    POPULAR_SEARCHES,
    diversify_ranked_results,
    infer_device_identity,
    merge_consumer_identities,
    prepare_catalog_search,
    search_catalog_frame,
)
from src.reporting import assessment_pdf, comparison_pdf
from src.train_model import FEATURES
from src.utils import CATEGORY_BASELINES, calculate_assessment, explanation, feature_defaults, has_product_environmental_evidence, impact_category, recommendations


ROOT = Path(__file__).parent
CATALOG_PREVIEW_LIMIT = 300
st.set_page_config(
    page_title="Luma · Gadget impact intelligence",
    page_icon="◌",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={"About": "Luma · Environmental Impact Analyser of Gadgets Using Deep Learning and AI"},
)


def _query_value(key: str, fallback: str = "") -> str:
    value = st.query_params.get(key, fallback)
    return value[-1] if isinstance(value, list) and value else str(value or fallback)


query_theme = _query_value("theme")
if "ui_theme" not in st.session_state:
    st.session_state.ui_theme = query_theme if query_theme in {"light", "dark"} else "light"
elif query_theme in {"light", "dark"} and query_theme != st.session_state.ui_theme:
    # A shared URL is authoritative when it changes in an existing session.
    st.session_state.ui_theme = query_theme
    st.session_state.theme_control = query_theme == "dark"
if "saved_gadgets" not in st.session_state:
    st.session_state.saved_gadgets = []


def _apply_theme_change() -> None:
    """Apply a theme toggle before the next render without a second forced rerun."""
    requested_theme = "dark" if st.session_state.get("theme_control", False) else "light"
    st.session_state.ui_theme = requested_theme
    st.query_params["theme"] = requested_theme

THEME = st.session_state.ui_theme
DARK = THEME == "dark"
palette = {
    "ink": "#edf7f1" if DARK else "#14241c",
    "muted": "#9eafa5" if DARK else "#64736b",
    "paper": "#09110e" if DARK else "#f3f6f2",
    "card": "#101a16" if DARK else "#ffffff",
    "card2": "#15231d" if DARK else "#f8fbf8",
    "line": "#263a30" if DARK else "#dce7df",
    "green": "#70e2a7" if DARK else "#126b42",
    "bright": "#4cdd93",
    "mint": "#16372a" if DARK else "#e3f7eb",
    "amber": "#f3bd62" if DARK else "#9a6717",
    "shadow": "0 22px 60px rgba(0,0,0,.34)" if DARK else "0 18px 50px rgba(28,72,49,.10)",
    "hero_a": "#0b2118" if DARK else "#0b2f21",
    "hero_b": "#133a29" if DARK else "#135f3c",
    "button_bg": "#4cdd93" if DARK else "#126b42",
    "button_ink": "#06120c" if DARK else "#ffffff",
    "table_bg": "#050a08" if DARK else "#ffffff",
    "table_alt": "#0a120e" if DARK else "#f7faf8",
    "table_head": "#101a16" if DARK else "#eef4f0",
    "table_ink": "#f5fbf7" if DARK else "#14241c",
}

css = r"""
<style>
@property --ring-progress{syntax:'<angle>';inherits:false;initial-value:0deg}
:root{
  --ink:__INK__;--muted:__MUTED__;--paper:__PAPER__;--card:__CARD__;--card2:__CARD2__;
  --line:__LINE__;--green:__GREEN__;--bright:__BRIGHT__;--mint:__MINT__;--amber:__AMBER__;--shadow:__SHADOW__;
  --button-bg:__BUTTON_BG__;--button-ink:__BUTTON_INK__;--table-bg:__TABLE_BG__;--table-alt:__TABLE_ALT__;
  --table-head:__TABLE_HEAD__;--table-ink:__TABLE_INK__;
  color-scheme:__COLOR_SCHEME__;
}
@keyframes page-in{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@keyframes rise{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
@keyframes float-device{0%,100%{transform:translateY(0) rotate(7deg)}50%{transform:translateY(-12px) rotate(4deg)}}
@keyframes orbit{to{transform:translate(-50%,-50%) rotate(360deg)}}
@keyframes breathe{0%,100%{opacity:.35;transform:scale(1)}50%{opacity:.62;transform:scale(1.12)}}
@keyframes shimmer{0%{transform:translateX(-150%) skewX(-20deg)}100%{transform:translateX(370%) skewX(-20deg)}}
@keyframes ring-fill{from{--ring-progress:0deg}to{--ring-progress:var(--ring-target)}}
html{scroll-behavior:smooth}.stApp{background:var(--paper);color:var(--ink)}
[data-testid="stAppViewContainer"]{background:radial-gradient(circle at 12% 0%,rgba(76,221,147,.075),transparent 28rem),var(--paper)}
[data-testid="stHeader"]{background:transparent}.block-container{max-width:1420px;padding:1.15rem 2rem 3.6rem;animation:page-in .48s ease both}.stMainBlockContainer{transform:none!important}
h1,h2,h3,h4,[data-testid="stMetricValue"]{font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif!important;letter-spacing:-.035em;color:var(--ink)}
p,label,span,div{font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.stCaption,.stMarkdown p{color:var(--muted)}
.top-brand{display:flex;align-items:center;gap:.78rem;padding:.42rem 0 .8rem}.brand-orb{width:31px;height:31px;border-radius:50%;border:1px solid #70e2a7;background:#09110e;position:relative;box-shadow:inset 0 0 0 7px #16372a}.brand-orb:after{content:"";position:absolute;width:7px;height:7px;border-radius:50%;background:var(--bright);right:-2px;top:2px}.brand-name{font-weight:800;font-size:.95rem;letter-spacing:.16em;color:var(--ink)}.brand-note{font-size:.71rem;color:var(--muted);border-left:1px solid var(--line);padding-left:.75rem}
.hero{position:relative;overflow:hidden;min-height:370px;border-radius:30px;padding:3.25rem 3.5rem;background:linear-gradient(125deg,__HERO_A__,__HERO_B__);box-shadow:0 30px 76px rgba(6,39,25,.22);isolation:isolate;color:white}
.hero:before{content:"";position:absolute;inset:0;background:radial-gradient(circle at 81% 28%,rgba(98,242,163,.19),transparent 28%),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px);background-size:auto,38px 38px,38px 38px;z-index:-1}.hero:after{content:"";position:absolute;width:320px;height:320px;border-radius:50%;right:-100px;bottom:-160px;background:#60e8a0;filter:blur(80px);opacity:.14;animation:breathe 6s ease-in-out infinite;z-index:-1}
.hero-content{position:relative;z-index:3;max-width:735px;animation:rise .7s .06s ease both}.eyebrow{display:inline-flex;align-items:center;gap:.48rem;padding:.34rem .7rem;border:1px solid rgba(152,242,191,.32);background:rgba(150,239,187,.10);border-radius:999px;color:#a7f4c8;font-size:.69rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase}.eyebrow:before{content:"";width:6px;height:6px;border-radius:50%;background:#65e7a0;box-shadow:0 0 0 5px rgba(101,231,160,.10)}
.hero h1{max-width:700px;color:white;font-size:clamp(2.55rem,5vw,4.55rem);line-height:.99;letter-spacing:-.064em;margin:1.05rem 0 .95rem}.hero p{max-width:650px;color:rgba(235,250,241,.79);font-size:1.05rem;line-height:1.62;margin:0}.badges{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:1.3rem}.badge{padding:.42rem .67rem;border-radius:10px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.11);color:#dcf9e8;font-size:.73rem;font-weight:600;backdrop-filter:blur(8px)}
.statbar{display:flex;flex-wrap:wrap;gap:.7rem;margin-top:1.65rem}.stat{min-width:124px;padding:.68rem .78rem;background:rgba(4,27,18,.28);border:1px solid rgba(255,255,255,.1);border-radius:13px;backdrop-filter:blur(10px)}.stat b{display:block;color:white;font-size:1.18rem;line-height:1.15}.stat span{display:block;color:#a9c9b7;font-size:.66rem;margin-top:.18rem}
.device-scene{position:absolute;right:3%;top:50%;width:330px;height:330px;transform:translateY(-50%);z-index:1}.device{position:absolute;left:50%;top:50%;width:120px;height:218px;border-radius:25px;transform:translate(-50%,-50%) rotate(7deg);background:linear-gradient(145deg,#2d473a,#0c1511);border:2px solid rgba(213,255,229,.32);box-shadow:-20px 28px 60px rgba(0,0,0,.35),inset 0 0 0 5px rgba(255,255,255,.04);animation:float-device 5.2s ease-in-out infinite}.device:before{content:"";position:absolute;inset:10px;border-radius:18px;background:radial-gradient(circle at 65% 25%,rgba(94,237,158,.52),transparent 32%),linear-gradient(155deg,#133625,#09110e)}.device:after{content:"";position:absolute;top:8px;left:50%;width:30px;height:4px;border-radius:4px;transform:translateX(-50%);background:#020706}.device-line{position:absolute;z-index:2;left:29px;right:29px;bottom:48px;height:3px;border-radius:3px;background:#61e69c;box-shadow:0 -14px 0 rgba(97,230,156,.42),0 -28px 0 rgba(97,230,156,.18)}.orbit{position:absolute;left:50%;top:50%;border:1px solid rgba(126,242,176,.22);border-radius:50%;transform:translate(-50%,-50%);animation:orbit 15s linear infinite}.orbit.one{width:250px;height:250px}.orbit.two{width:320px;height:320px;animation-duration:23s;animation-direction:reverse}.orbit i{position:absolute;width:8px;height:8px;border-radius:50%;top:-4px;left:50%;background:#72efa9;box-shadow:0 0 18px #64e69e}
.signal-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:.8rem;margin:1rem 0 1.35rem}.signal-card{display:flex;align-items:center;gap:.75rem;background:var(--card);border:1px solid var(--line);border-radius:15px;padding:.86rem 1rem;box-shadow:0 8px 24px rgba(16,50,31,.045);transition:transform .25s ease,border-color .25s ease,box-shadow .25s ease}.signal-card:hover{transform:translateY(-4px);border-color:rgba(76,221,147,.55);box-shadow:var(--shadow)}.signal-icon{display:grid;place-items:center;flex:0 0 34px;height:34px;border-radius:11px;background:var(--mint);color:var(--green);font-size:.67rem;font-weight:800}.signal-card b{display:block;color:var(--ink);font-size:.82rem}.signal-card div span{display:block;color:var(--muted);font-size:.69rem;margin-top:.12rem}
.result-count{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin:.5rem 0 .7rem;padding:.69rem .86rem;border-radius:13px;background:var(--mint);border:1px solid var(--line);color:var(--ink)}.result-count strong{font-size:.86rem}.result-count span{font-size:.72rem;color:var(--muted)}
.score-card{display:flex;align-items:center;gap:1.2rem;min-height:178px;padding:1.35rem;background:var(--card);border:1px solid var(--line);border-radius:21px;box-shadow:var(--shadow);animation:rise .55s ease both}.score-ring{--ring-progress:var(--ring-target);display:grid;place-items:center;position:relative;width:126px;height:126px;flex:0 0 126px;border-radius:50%;background:conic-gradient(var(--ring-color) var(--ring-progress),var(--line) 0);animation:ring-fill 1.15s .12s cubic-bezier(.2,.8,.2,1) both}.score-ring:before{content:"";position:absolute;inset:11px;border-radius:50%;background:var(--card);box-shadow:inset 0 0 0 1px var(--line)}.score-value{position:relative;text-align:center;color:var(--ink);font-size:1.92rem;font-weight:800;line-height:.9;letter-spacing:-.05em}.score-value small{font-size:.66rem;color:var(--muted);letter-spacing:0}.score-copy b{display:block;color:var(--ink);font-size:1.05rem}.score-copy span{display:block;color:var(--muted);font-size:.72rem;margin-top:.18rem}.score-chip{display:inline-flex!important;width:max-content;padding:.27rem .5rem;border-radius:999px;background:var(--mint);color:var(--green)!important;font-weight:700;margin-top:.65rem!important}
.kpi-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:.75rem;height:100%}.kpi{position:relative;overflow:hidden;display:flex;flex-direction:column;justify-content:center;min-height:178px;padding:1.2rem;background:var(--card);border:1px solid var(--line);border-radius:20px;transition:transform .25s ease,border-color .25s ease;animation:rise .55s ease both}.kpi:nth-child(2){animation-delay:.07s}.kpi:nth-child(3){animation-delay:.14s}.kpi:hover{transform:translateY(-4px);border-color:rgba(76,221,147,.5)}.kpi:after{content:"";position:absolute;right:-24px;bottom:-30px;width:78px;height:78px;border-radius:50%;background:var(--mint)}.kpi-label{position:relative;z-index:1;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);font-size:.61rem;font-weight:700}.kpi-value{position:relative;z-index:1;color:var(--ink);font-size:1.7rem;font-weight:800;letter-spacing:-.055em;line-height:1.02;margin:.56rem 0 .36rem}.kpi-value small{font-size:.64rem;letter-spacing:0}.kpi-delta{position:relative;z-index:1;color:var(--green);font-size:.66rem;font-weight:600}
.callout{position:relative;overflow:hidden;background:linear-gradient(135deg,var(--mint),var(--card));border:1px solid var(--line);border-left:4px solid var(--bright);padding:1.05rem 1.2rem;border-radius:14px;color:var(--ink);line-height:1.55;margin:.85rem 0}.callout b{color:var(--green)}.callout.warning{border-left-color:var(--amber)}
.field-row{display:flex;justify-content:space-between;gap:1rem;border-bottom:1px solid var(--line);padding:.67rem .2rem;color:var(--ink);transition:padding .2s ease,background .2s ease}.field-row:hover{padding-left:.65rem;padding-right:.65rem;background:var(--card2);border-radius:9px}.observed{color:var(--green);font-weight:700}.estimated{color:var(--amber);font-weight:700}.tiny{color:var(--muted);font-size:.76rem}.source-card{padding:1rem 1.15rem;background:var(--card);border:1px solid var(--line);border-radius:15px;margin:.48rem 0;transition:transform .2s ease,border-color .2s ease}.source-card:hover{transform:translateX(4px);border-color:rgba(76,221,147,.48)}.source-card a{color:var(--green);font-weight:700;text-decoration:none}.source-card span{display:block;color:var(--muted);font-size:.72rem;margin-top:.22rem}
.saved-card{padding:1rem 1.1rem;background:var(--card);border:1px solid var(--line);border-radius:16px;margin:.5rem 0}.saved-card strong{color:var(--ink)}.saved-card span{color:var(--muted);font-size:.74rem}.method-flow{display:grid;grid-template-columns:1fr auto 1fr auto 1fr;gap:.65rem;align-items:center;margin:1rem 0}.flow-node{padding:1rem;background:var(--card);border:1px solid var(--line);border-radius:15px;text-align:center;color:var(--ink);font-weight:700;font-size:.78rem}.flow-node span{display:block;color:var(--muted);font-weight:400;font-size:.68rem;margin-top:.25rem}.flow-arrow{color:var(--green);font-weight:900}
button[kind="primary"],.stDownloadButton button,.stLinkButton a{border-radius:11px!important;transition:transform .2s ease,box-shadow .2s ease!important}.stDownloadButton button{background:var(--button-bg)!important;border:1px solid var(--button-bg)!important;color:var(--button-ink)!important}.stDownloadButton button p,.stDownloadButton button span,.stDownloadButton button div{color:var(--button-ink)!important;opacity:1!important;font-weight:750!important}.stLinkButton a{background:var(--card2)!important;border:1px solid var(--line)!important;color:var(--ink)!important}.stLinkButton a p,.stLinkButton a span,.stLinkButton a div{color:var(--ink)!important;opacity:1!important}.stDownloadButton button:hover,.stLinkButton a:hover,button[kind="primary"]:hover{transform:translateY(-2px);box-shadow:0 10px 22px rgba(22,111,69,.24)}.stDownloadButton button:hover{filter:brightness(1.06)}.stLinkButton a:hover{background:var(--mint)!important;border-color:var(--bright)!important}
button[kind="secondary"]{background:var(--card2)!important;border-color:var(--line)!important;color:var(--ink)!important}button[kind="secondary"] p,button[kind="secondary"] span,button[kind="secondary"] div{color:var(--ink)!important;opacity:1!important}button[kind="secondary"]:hover{background:var(--mint)!important;border-color:var(--bright)!important}
.stTabs > div > div:has(> [data-baseweb="tab-list"]){position:sticky;top:.55rem;z-index:100}.stTabs [data-baseweb="tab-list"]{position:relative;gap:.25rem;padding:.36rem;background:var(--card);border:1px solid var(--line);border-radius:15px;margin-bottom:1rem;overflow-x:auto;box-shadow:0 12px 32px rgba(0,0,0,.16);backdrop-filter:blur(16px)}.stTabs [data-baseweb="tab"]{padding:.55rem .88rem;color:var(--muted);border-radius:10px;transition:background .2s ease,color .2s ease,transform .2s ease}.stTabs [data-baseweb="tab"]:hover{background:var(--mint);color:var(--green);transform:translateY(-1px)}.stTabs [aria-selected="true"]{color:var(--green)!important;background:var(--card2)!important;box-shadow:0 5px 14px rgba(24,72,45,.08)}.stTabs [data-baseweb="tab"] p{color:inherit!important;white-space:nowrap}
.stTabs [data-baseweb="tab-highlight"]{display:none}
.stTextInput input,.stNumberInput input,[data-baseweb="select"]>div,.stMultiSelect [data-baseweb="select"]>div{border-radius:11px!important;background:var(--card)!important;color:var(--ink)!important;border-color:var(--line)!important}.stTextInput input::placeholder,.stNumberInput input::placeholder,[data-baseweb="select"] input::placeholder{color:var(--muted)!important;opacity:1!important}[data-baseweb="select"] div{color:var(--ink)!important}[data-baseweb="popover"] [role="listbox"],[data-baseweb="menu"]{background:var(--card)!important;border:1px solid var(--line)!important}[role="option"]{background:var(--card)!important;color:var(--ink)!important}[role="option"]:hover,[role="option"][aria-selected="true"]{background:var(--mint)!important}.stTextInput input:focus,.stNumberInput input:focus,[data-baseweb="select"]>div:focus-within{border-color:var(--bright)!important;box-shadow:0 0 0 3px rgba(76,221,147,.13)!important}[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:16px;overflow:hidden;box-shadow:0 8px 28px rgba(25,60,39,.05)}[data-testid="stExpander"]{background:var(--card);border-color:var(--line)!important;border-radius:14px!important}[data-testid="stMetric"]{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:.8rem}
[data-testid="stToggle"] label p,[data-testid="stWidgetLabel"] p,[data-testid="stRadio"] label p,[data-testid="stCheckbox"] label p{color:var(--ink)!important;opacity:1!important}[data-testid="stMetricLabel"] p{color:var(--muted)!important;opacity:1!important}.stAlert{border-radius:14px;background:var(--card2)!important;border:1px solid var(--line)!important}.stAlert p,.stAlert div{color:var(--ink)!important;opacity:1!important}.stCodeBlock{border:1px solid var(--line);border-radius:12px;overflow:hidden}
.data-table-shell{width:100%;max-height:520px;overflow:auto;border:1px solid var(--line);border-radius:16px;background:var(--table-bg);box-shadow:0 12px 34px rgba(0,0,0,.18);scrollbar-color:var(--green) var(--table-bg);scrollbar-width:thin;-webkit-overflow-scrolling:touch}.data-table{width:100%;min-width:1180px;border-collapse:separate;border-spacing:0;background:var(--table-bg);color:var(--table-ink);font-size:.76rem;line-height:1.35}.data-table th{position:sticky;top:0;z-index:2;padding:.72rem .7rem;background:var(--table-head);color:var(--table-ink);border-right:1px solid var(--line);border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;font-weight:750}.data-table td{max-width:290px;padding:.65rem .7rem;background:var(--table-bg);color:var(--table-ink);border-right:1px solid var(--line);border-bottom:1px solid var(--line);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.data-table tbody tr:nth-child(even) td{background:var(--table-alt)}.data-table tbody tr:hover td{background:var(--mint);color:var(--ink)}.data-table th:last-child,.data-table td:last-child{border-right:0}.data-table a{color:var(--green);font-weight:750;text-decoration:none}.data-table a:hover{text-decoration:underline}
@media(max-width:1080px){.device-scene{right:-4%;opacity:.58}.hero-content{max-width:72%}.score-card{flex-direction:column;text-align:center}.score-copy{display:flex;flex-direction:column;align-items:center}.kpi-grid{grid-template-columns:1fr}.kpi{min-height:112px}.method-flow{grid-template-columns:1fr}.flow-arrow{transform:rotate(90deg);text-align:center}}
@media(min-width:761px){[data-testid="stHorizontalBlock"]:has(.top-brand){margin-top:1.65rem}}
@media(max-width:760px){.block-container{padding:.75rem .82rem 2.4rem}.brand-note{display:none}.hero{min-height:auto;padding:2rem 1.35rem;border-radius:23px}.hero-content{max-width:100%}.hero h1{font-size:2.55rem}.hero p{font-size:.92rem;max-width:88%}.device-scene{right:-126px;top:46%;opacity:.22}.statbar{gap:.42rem}.stat{min-width:calc(50% - .25rem);padding:.58rem .65rem}.signal-strip{grid-template-columns:1fr;gap:.5rem}.signal-card{padding:.72rem .8rem}.stTabs > div > div:has(> [data-baseweb="tab-list"]){position:static}.stTabs [data-baseweb="tab-list"]{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr));position:static;overflow:visible;gap:.3rem}.stTabs [data-baseweb="tab"]{justify-content:center;padding:.48rem .42rem;min-width:0}.stTabs [data-baseweb="tab"] p{font-size:.68rem!important;white-space:normal;text-align:center;line-height:1.25}.score-card{flex-direction:column;text-align:center}.score-copy{display:flex;flex-direction:column;align-items:center}.kpi-grid{grid-template-columns:1fr;gap:.5rem}.kpi{min-height:108px}.result-count{align-items:flex-start;flex-direction:column;gap:.2rem}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation-duration:.01ms!important;animation-iteration-count:1!important;scroll-behavior:auto!important;transition-duration:.01ms!important}}
</style>
"""
replacements = {
    "__INK__": palette["ink"], "__MUTED__": palette["muted"], "__PAPER__": palette["paper"],
    "__CARD__": palette["card"], "__CARD2__": palette["card2"], "__LINE__": palette["line"],
    "__GREEN__": palette["green"], "__BRIGHT__": palette["bright"], "__MINT__": palette["mint"],
    "__AMBER__": palette["amber"], "__SHADOW__": palette["shadow"], "__HERO_A__": palette["hero_a"],
    "__HERO_B__": palette["hero_b"], "__BUTTON_BG__": palette["button_bg"],
    "__BUTTON_INK__": palette["button_ink"], "__TABLE_BG__": palette["table_bg"],
    "__TABLE_ALT__": palette["table_alt"], "__TABLE_HEAD__": palette["table_head"],
    "__TABLE_INK__": palette["table_ink"], "__COLOR_SCHEME__": "dark" if DARK else "light",
}
for token, value in replacements.items():
    css = css.replace(token, value)
st.markdown(css, unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load_data():
    catalog = prepare_catalog_search(
        merge_consumer_identities(pd.read_csv(ROOT / "data" / "official_gadgets.csv", low_memory=False))
    )
    metadata = json.loads((ROOT / "data" / "source_metadata.json").read_text())
    grid = pd.read_csv(ROOT / "data" / "grid_intensity.csv")
    repairs = pd.read_csv(ROOT / "data" / "repair_profiles.csv")
    metrics = json.loads((ROOT / "models" / "metrics.json").read_text())
    return catalog, metadata, grid, repairs, metrics


@st.cache_resource(show_spinner=False)
def load_model():
    path = ROOT / "models" / "gadget_impact_pipeline.joblib"
    return joblib.load(path) if path.exists() else None


catalog, metadata, grid_data, repair_profiles, model_metrics = load_data()
model = load_model()


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().casefold() in {"true", "1", "yes"}


def _missing(value) -> bool:
    return value is None or (not isinstance(value, (list, dict)) and pd.isna(value))


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return cleaned[:80] or "gadget"


def product_label(row: pd.Series) -> str:
    suffix = str(row.get("product_id", ""))[-7:]
    return f"{row['manufacturer']} · {row['name']} · {row['model_number']} · {suffix}"


def search_catalog(
    query: str,
    categories: list[str] | None = None,
    sources: list[str] | None = None,
    brands: list[str] | None = None,
    primary_only: bool = False,
    limit: int | None = None,
) -> pd.DataFrame:
    return search_catalog_frame(
        catalog,
        query,
        categories=categories,
        sources=sources,
        manufacturers=brands,
        primary_only=primary_only,
        limit=limit,
    )


def _table_value(value) -> str:
    if _missing(value):
        return "—"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value)


def render_data_table(
    frame: pd.DataFrame,
    *,
    height: int = 520,
    min_width: int = 760,
    link_columns: set[str] | None = None,
    aria_label: str = "Data table",
) -> None:
    """Render a compact, theme-native table with predictable dark-mode contrast."""
    links = link_columns or set()
    headers = "".join(f"<th scope='col'>{html.escape(str(column))}</th>" for column in frame.columns)
    body_rows: list[str] = []
    for row in frame.itertuples(index=False, name=None):
        cells: list[str] = []
        for column, value in zip(frame.columns, row):
            display = _table_value(value)
            escaped = html.escape(display)
            if column in links and display.startswith(("https://", "http://")):
                content = f'<a href="{html.escape(display, quote=True)}" target="_blank" rel="noopener noreferrer">Open ↗</a>'
            else:
                content = escaped
            cells.append(f'<td title="{html.escape(display, quote=True)}">{content}</td>')
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    markup = (
        f'<div class="data-table-shell" role="region" aria-label="{html.escape(aria_label, quote=True)}" '
        f'tabindex="0" style="max-height:{int(height)}px">'
        f'<table class="data-table" style="min-width:{int(min_width)}px"><thead><tr>{headers}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody></table></div>'
    )
    st.markdown(markup, unsafe_allow_html=True)


def product_values(row: pd.Series) -> dict:
    values = row.to_dict()
    defaults = feature_defaults(values["category"])
    daily_hours = 24.0 if values["category"] == "Router / network" else 6.0 if values["category"] in {"Laptop", "Desktop", "Monitor"} else 5.0
    default_fields = {
        **defaults,
        "daily_hours": daily_hours,
        "repairability": 5.0,
        "recyclability_pct": 65.0,
        "recycled_content_pct": 20.0,
        "weight_kg": defaults["weight_kg"],
        "transport_km": 7000.0,
        "grid_kg_co2_per_kwh": 0.42,
        "software_support_years": None,
    }
    for key, default in default_fields.items():
        if key not in values or _missing(values.get(key)):
            values[key] = default
    for field in ("replaceable_battery", "observed_energy", "observed_repairability", "observed_carbon", "observed_battery", "observed_durability", "observed_software_support"):
        values[field] = _bool(values.get(field))
    values["catalog_product"] = True
    return values


def assess(values: dict):
    prediction, error = None, None
    if model is not None:
        model_features = getattr(model, "features", FEATURES)
        frame = pd.DataFrame([{feature: values.get(feature) for feature in model_features}])
        if hasattr(model, "predict_with_uncertainty"):
            predictions, errors = model.predict_with_uncertainty(frame)
            prediction, error = float(predictions[0]), float(errors[0])
        else:
            prediction = float(model.predict(frame)[0])
    return calculate_assessment(values, prediction, error)


def provenance(values: dict) -> list[tuple[str, str]]:
    override_axes = {str(axis) for axis in values.get("override_axes", [])}

    def evidence_state(axis: str, observed: bool) -> str:
        return "Scenario override" if axis in override_axes else "Observed" if observed else "Estimated"

    return [
        ("Product identity", "Observed" if values.get("catalog_product") else "Inferred" if values.get("identity_confidence") in {"High", "Moderate"} else "Scenario"),
        ("Energy", evidence_state("energy", bool(values.get("observed_energy")))),
        ("Repairability", evidence_state("repairability", bool(values.get("observed_repairability")))),
        ("Lifecycle carbon", "Observed" if values.get("observed_carbon") else "Estimated"),
        ("Battery", evidence_state("battery", bool(values.get("observed_battery")))),
        ("Durability", "Observed" if values.get("observed_durability") else "Estimated"),
        ("Materials & transport", "Scenario"),
    ]


def _changed(original, current, tolerance: float = 1e-8) -> bool:
    try:
        return abs(float(original) - float(current)) > tolerance
    except (TypeError, ValueError):
        return original != current


def _mark_override(values: dict, axis: str, *, invalidated_observation: bool = False) -> None:
    axes = {str(item) for item in values.get("override_axes", [])}
    axes.add(axis)
    values["override_axes"] = sorted(axes)
    if invalidated_observation:
        invalidated = {str(item) for item in values.get("invalidated_observed_axes", [])}
        invalidated.add(axis)
        values["invalidated_observed_axes"] = sorted(invalidated)


def factor_chart(result) -> alt.Chart:
    frame = pd.DataFrame({"Factor": result.factors.keys(), "Burden": result.factors.values()}).sort_values("Burden")
    return (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=7, height=18)
        .encode(
            x=alt.X("Burden:Q", scale=alt.Scale(domain=[0, 100]), axis=alt.Axis(title="Modeled burden / 100", grid=True)),
            y=alt.Y("Factor:N", sort=None, axis=alt.Axis(title=None, labelLimit=190)),
            color=alt.Color("Burden:Q", scale=alt.Scale(domain=[0, 100], range=["#4cdd93", "#e1a84e"]), legend=None),
            tooltip=["Factor", alt.Tooltip("Burden:Q", format=".1f")],
        )
        .properties(height=230)
        .configure(background=palette["card"])
        .configure_view(strokeWidth=0)
        .configure_axis(labelColor=palette["muted"], titleColor=palette["muted"], gridColor=palette["line"], domain=False, tickColor=palette["line"])
    )


def saved_record(values: dict, result) -> dict:
    return {
        "id": str(values.get("product_id") or f"custom-{_safe_name(values.get('manufacturer', 'unknown'))}-{_safe_name(values.get('name', 'gadget'))}"),
        "product": f"{values.get('manufacturer', 'Unknown')} {values.get('name', 'Gadget')}",
        "model_number": values.get("model_number", ""),
        "category": values.get("category", "Other"),
        "eco_score": result.eco_score,
        "score_low": result.score_low,
        "score_high": result.score_high,
        "lifecycle_carbon": result.lifecycle_carbon,
        "annual_energy": result.annual_energy,
        "confidence": result.confidence,
        "source": values.get("source_name", "User scenario"),
        "source_url": values.get("source_url", ""),
    }


def unlisted_device_values(name: str, manufacturer: str, category: str, identity_confidence: str) -> dict:
    """Create an explicit category-level scenario for any named device."""
    defaults = feature_defaults(category)
    daily_hours = 24.0 if category == "Router / network" else 6.0 if category in {"Laptop", "Desktop", "Monitor"} else 5.0
    return {
        "name": name.strip() or "Unlisted gadget",
        "model_number": name.strip() or "Unlisted gadget",
        "manufacturer": manufacturer.strip() or "Unknown",
        "category": category,
        "source_name": f"{category} baseline estimate",
        "source_url": "",
        "source_type": "scenario",
        "market_date": "",
        "observed_energy": False,
        "observed_repairability": False,
        "observed_carbon": False,
        "observed_battery": False,
        "observed_durability": False,
        "observed_software_support": False,
        "observed_field_count": 0,
        "annual_energy_kwh": None,
        **defaults,
        "daily_hours": daily_hours,
        "grid_profile": "Average",
        "grid_kg_co2_per_kwh": 0.42,
        "repairability": 5.0,
        "recyclability_pct": 65.0,
        "recycled_content_pct": 20.0,
        "replaceable_battery": False,
        "transport_km": 7000.0,
        "catalog_product": False,
        "resolution_status": "category_estimate" if category != "Other" else "generic_estimate",
        "identity_confidence": identity_confidence,
    }


def _switch_to_unlisted_estimate(name: str) -> None:
    st.session_state.analysis_input_mode = "Any device estimate"
    st.session_state.unlisted_device_name = name


def _switch_to_catalog_search(name: str) -> None:
    st.session_state.analysis_input_mode = "Search catalogue"
    st.session_state.analysis_search = name


def best_peers(values: dict, limit: int = 3) -> list[tuple[dict, object]]:
    pool = catalog[(catalog["category"] == values["category"]) & (catalog["product_id"] != values.get("product_id", ""))].copy()
    if "is_primary_record" in pool:
        pool = pool[pool["is_primary_record"].map(_bool)]
    if values.get("observed_carbon") and "reported_lifecycle_kg" in pool:
        candidates = pool[pool["observed_carbon"].map(_bool) & pool["reported_lifecycle_kg"].notna()].sort_values("reported_lifecycle_kg").head(12)
    elif values.get("observed_repairability"):
        candidates = pool[pool["observed_repairability"].map(_bool)].sort_values("repairability", ascending=False).head(12)
    else:
        candidates = pool[pool["observed_energy"].map(_bool)].sort_values("active_power_w").head(12)
    ranked = []
    for _, row in candidates.iterrows():
        peer_values = product_values(row)
        peer_values["grid_kg_co2_per_kwh"] = values["grid_kg_co2_per_kwh"]
        peer_result = assess(peer_values)
        ranked.append((peer_values, peer_result))
    return sorted(ranked, key=lambda item: item[1].eco_score, reverse=True)[:limit]


# Compact top bar and persistent theme control.
brand_column, theme_column = st.columns([5, 1])
with brand_column:
    st.markdown('<div class="top-brand"><span class="brand-orb"></span><span class="brand-name">LUMA</span><span class="brand-note">Environmental intelligence for the technology you keep</span></div>', unsafe_allow_html=True)
with theme_column:
    st.toggle("Dark mode", value=DARK, key="theme_control", on_change=_apply_theme_change)
if _query_value("theme") != THEME:
    st.query_params["theme"] = THEME


category_count = catalog["category"].nunique()
manufacturer_count = catalog["_search_manufacturer"].nunique()
unique_entities = catalog["entity_key"].nunique() if "entity_key" in catalog else len(catalog)
hero_markup = f"""
    <section class="hero">
      <div class="hero-content">
        <div class="eyebrow">Evidence-first gadget intelligence</div>
        <h1>See the impact behind every device.</h1>
        <p>Search real product records, personalise how a gadget is used, and understand the evidence and uncertainty behind every AI-assisted score.</p>
        <div class="badges"><span class="badge">Global device search</span><span class="badge">Regulatory data</span><span class="badge">Category-aware neural models</span><span class="badge">Any-device estimates</span><span class="badge">Explainable uncertainty</span></div>
        <div class="statbar">
          <div class="stat"><b>{len(catalog):,}</b><span>catalog records</span></div>
          <div class="stat"><b>{unique_entities:,}</b><span>resolved gadgets</span></div>
          <div class="stat"><b>{manufacturer_count:,}</b><span>manufacturers</span></div>
          <div class="stat"><b>{len(grid_data):,}</b><span>regional grid profiles</span></div>
          <div class="stat"><b>{metadata['snapshot_date']}</b><span>data snapshot</span></div>
        </div>
      </div>
      <div class="device-scene" aria-hidden="true"><div class="orbit one"><i></i></div><div class="orbit two"><i></i></div><div class="device"><span class="device-line"></span></div></div>
    </section>
    <div class="signal-strip">
      <div class="signal-card"><span class="signal-icon">01</span><div><b>Find a real model</b><span>Phones, laptops, tablets, TVs and more</span></div></div>
      <div class="signal-card"><span class="signal-icon">02</span><div><b>Personalise the lifecycle</b><span>Region, use, lifespan, repair and transport</span></div></div>
      <div class="signal-card"><span class="signal-icon">03</span><div><b>Act with context</b><span>Ranges, evidence quality and greener peers</span></div></div>
    </div>
    """


tab_labels = ["Explore data", "Analyse a gadget", "Compare", "Saved & reports", "AI & data quality"]
default_tab = "Analyse a gadget" if _query_value("tab") == "analyse" else "Explore data"
explore, analyse, compare, saved, intelligence = st.tabs(tab_labels, default=default_tab)


with explore:
    st.markdown(hero_markup, unsafe_allow_html=True)
    st.subheader("Explore the living data snapshot")
    st.caption("Search source-backed identities and measurements. Estimated lifecycle fields appear only in the assessment experience.")
    f1, f2, f3 = st.columns([1.55, 1, 1])
    with f1:
        browse_query = st.text_input("Search manufacturer, model or identifier", placeholder="Try Pixel, Galaxy, ThinkPad, iPad…", key="browse_search")
    with f2:
        browse_categories = st.multiselect("Categories", sorted(catalog["category"].unique()), default=sorted(catalog["category"].unique()), key="browse_categories")
    with f3:
        browse_sources = st.multiselect("Sources", sorted(catalog["source_name"].unique()), default=sorted(catalog["source_name"].unique()), key="browse_sources")
    primary_only = st.toggle("One primary record per resolved model", value=True, help="Complementary records remain in the downloadable snapshot.")
    browse = search_catalog(browse_query, browse_categories, browse_sources, primary_only=primary_only)
    browse_manufacturer_count = browse["_search_manufacturer"].nunique() if "_search_manufacturer" in browse else browse.manufacturer.nunique()
    st.markdown(f'<div class="result-count"><strong>{len(browse):,} matching records</strong><span>{browse_manufacturer_count:,} manufacturers · snapshot {metadata["snapshot_id"]}</span></div>', unsafe_allow_html=True)
    columns = ["manufacturer", "name", "model_number", "category", "source_name", "market_date", "annual_energy_kwh", "repairability", "data_quality", "freshness_status", "source_url"]
    available_columns = [column for column in columns if column in browse]
    view = browse[available_columns].head(CATALOG_PREVIEW_LIMIT).rename(
        columns={
            "manufacturer": "Manufacturer", "name": "Model", "model_number": "Model number", "category": "Category",
            "source_name": "Data source", "market_date": "Market / index date", "annual_energy_kwh": "Annual energy (kWh)",
            "repairability": "Repairability / 10", "data_quality": "Evidence", "freshness_status": "Freshness", "source_url": "Source",
        }
    )
    render_data_table(view, height=520, min_width=1180, link_columns={"Source"}, aria_label="Filtered gadget records")
    if len(browse) > CATALOG_PREVIEW_LIMIT:
        st.caption(f"Showing the first {CATALOG_PREVIEW_LIMIT:,} matches. Refine the search or download the full filtered result set.")
    export_columns = [column for column in browse.columns if not column.startswith("_search") and column != "search_aliases"]
    st.download_button("Download filtered records", browse[export_columns].to_csv(index=False).encode(), "luma-gadget-data-filtered.csv", "text/csv")


with analyse:
    controls, dashboard = st.columns([0.84, 2.16], gap="large")
    values = None
    with controls:
        st.subheader("Build an assessment")
        linked_product = _query_value("product")
        linked_region_query = _query_value("region")
        linked_row = catalog[catalog["product_id"].astype(str).eq(linked_product)] if linked_product else pd.DataFrame()
        if linked_product and st.session_state.get("_linked_product_seen") != linked_product:
            st.session_state._linked_product_seen = linked_product
            st.session_state.analysis_input_mode = "Search catalogue"
            if len(linked_row):
                st.session_state.analysis_search = str(linked_row.iloc[0]["model_number"])
            st.session_state.analysis_categories = []
            st.session_state.analysis_brands = []
        elif not linked_product:
            st.session_state._linked_product_seen = ""
        available_region_names = set(grid_data["region"].dropna().astype(str)) | {"Custom intensity"}
        if linked_region_query in available_region_names and st.session_state.get("_linked_region_seen") != linked_region_query:
            st.session_state._linked_region_seen = linked_region_query
            st.session_state.pop("analysis_region", None)
        mode = st.radio(
            "Input",
            ["Search catalogue", "Any device estimate"],
            horizontal=True,
            key="analysis_input_mode",
            help="Search all categories at once, or estimate a device that has no verified catalog record.",
        )
        if mode == "Search catalogue":
            query = st.text_input(
                "Search any device",
                placeholder="MacBook, iPhone 16, Galaxy S25, model number…",
                key="analysis_search",
            )
            st.caption("Searches every category and manufacturer. Try: " + " · ".join(POPULAR_SEARCHES[:6]))
            with st.expander("Optional search filters"):
                filter_categories = st.multiselect(
                    "Categories",
                    sorted(catalog["category"].unique()),
                    placeholder="All categories",
                    key="analysis_categories",
                )
                manufacturer_options = sorted(
                    {
                        min(group.astype(str), key=lambda value: (value.isupper(), len(value), value))
                        for _, group in catalog.groupby("_search_manufacturer")["manufacturer"]
                    },
                    key=str.casefold,
                )
                filter_brands = st.multiselect(
                    "Manufacturers",
                    manufacturer_options,
                    placeholder="All manufacturers",
                    key="analysis_brands",
                )
            if query.strip():
                all_matches = search_catalog(
                    query,
                    filter_categories,
                    None,
                    filter_brands,
                    primary_only=True,
                )
                matches = diversify_ranked_results(all_matches, 120)
                if len(all_matches) == 0:
                    st.warning(f'No verified record matched “{query}”. You can still assess it using transparent category assumptions.')
                    st.button(
                        f'Estimate “{query}”',
                        type="primary",
                        width="stretch",
                        on_click=_switch_to_unlisted_estimate,
                        args=(query,),
                    )
                else:
                    category_counts = all_matches["category"].value_counts()
                    categories_found = " · ".join(f"{category} {count:,}" for category, count in category_counts.head(5).items())
                    visible_note = f" · showing {len(matches):,} diverse candidates" if len(all_matches) > len(matches) else ""
                    st.caption(f"{len(all_matches):,} matches · {categories_found}{visible_note}")
                    labels = [(product_label(row), index) for index, row in matches.iterrows()]
                    label_names = [item[0] for item in labels]
                    linked_index = next(
                        (
                            position
                            for position, (_, row_index) in enumerate(labels)
                            if str(catalog.loc[row_index, "product_id"]) == linked_product
                        ),
                        0,
                    )
                    selected_label = st.selectbox(
                        "Best matching records",
                        label_names,
                        index=linked_index,
                        help="Results rank exact names and identifiers first, then aliases and cautious typo matches.",
                    )
                    selected_index = dict(labels)[selected_label]
                    values = product_values(catalog.loc[selected_index])
                    values["resolution_status"] = "catalog_match" if has_product_environmental_evidence(values) else "verified_identity_estimate"
                    with st.expander("Device not shown?"):
                        st.caption("Keep the name you entered and create a clearly labelled category-level estimate.")
                        st.button(
                            f'Estimate “{query}” instead',
                            width="stretch",
                            on_click=_switch_to_unlisted_estimate,
                            args=(query,),
                            key="estimate_instead",
                        )
            else:
                st.info("Enter a product family, retail name, model number or manufacturer. You no longer need to choose its category first.")
        else:
            if "unlisted_device_name" not in st.session_state:
                st.session_state.unlisted_device_name = ""
            fallback_name = st.text_input(
                "Device or model name",
                placeholder="Any phone, laptop, wearable, console or gadget",
                key="unlisted_device_name",
            )
            inferred = infer_device_identity(fallback_name)
            if st.session_state.get("_identity_seed") != fallback_name:
                st.session_state._identity_seed = fallback_name
                st.session_state.unlisted_manufacturer = inferred["manufacturer"]
                st.session_state.unlisted_category = inferred["category"]
            selected_category = st.selectbox("Device category", list(CATEGORY_BASELINES), key="unlisted_category")
            fallback_manufacturer = st.text_input("Manufacturer", key="unlisted_manufacturer")
            if fallback_name.strip():
                verified_hints = search_catalog(fallback_name, primary_only=True, limit=3)
                if len(verified_hints):
                    st.info("Verified catalog matches exist for this name. You can return to catalog search for stronger evidence.")
                    st.button(
                        "Show verified matches",
                        width="stretch",
                        on_click=_switch_to_catalog_search,
                        args=(fallback_name,),
                    )
                if selected_category == "Other":
                    st.warning("Choose the closest category if possible. Generic electronics estimates have the widest uncertainty.")
                values = unlisted_device_values(
                    fallback_name,
                    fallback_manufacturer,
                    selected_category,
                    inferred["identity_confidence"],
                )
                st.caption("No model-specific facts are assumed. Category defaults remain editable below and are labelled as estimates.")
            else:
                st.info("Type any device name. The app will suggest a manufacturer and category, then expose every assumption.")

        if values is not None:
            values.setdefault("override_axes", [])
            values.setdefault("invalidated_observed_axes", [])
            st.markdown("#### Your context")
            regions = sorted(grid_data["region"].dropna().unique().tolist())
            linked_region = _query_value("region", "India")
            region_index = regions.index(linked_region) if linked_region in regions else regions.index("India") if "India" in regions else 0
            region = st.selectbox("Electricity region", regions + ["Custom intensity"], index=region_index, key="analysis_region")
            if region == "Custom intensity":
                grid_factor = st.number_input("Grid intensity (g CO₂e / kWh)", 0.0, 2000.0, 420.0, 1.0) / 1000
                grid_year = "custom"
            else:
                region_row = grid_data[grid_data["region"].eq(region)].sort_values("year").iloc[-1]
                grid_factor = float(region_row["kg_co2e_per_kwh"])
                grid_year = int(region_row["year"])
            values["grid_kg_co2_per_kwh"] = grid_factor
            st.caption(f"{grid_factor * 1000:,.0f} g CO₂e/kWh · {grid_year} electricity data")
            values["daily_hours"] = st.slider("Daily active use", 0.5, 24.0, float(values.get("daily_hours", 5.0)), 0.5)
            values["lifespan_years"] = st.slider("Expected ownership", 1.0, 18.0, float(values["lifespan_years"]), 0.1)
            if values.get("observed_energy") and not _missing(values.get("annual_energy_kwh")):
                use_certified = st.toggle("Use published annual energy", value=True, help="Turn off to calculate energy from active power and your daily-use setting.")
                if not use_certified:
                    values["annual_energy_kwh"] = None
                    values["observed_energy"] = False
                    _mark_override(values, "energy", invalidated_observation=True)
            with st.expander("Advanced lifecycle assumptions"):
                st.caption("Changing a sourced field creates a scenario override; the original source remains linked below.")
                values["manufacturing_kg"] = st.number_input("Manufacturing carbon (kg CO₂e)", 0.0, 5000.0, float(values["manufacturing_kg"]))
                original_power = values["active_power_w"]
                power_was_observed = bool(values.get("observed_energy"))
                values["active_power_w"] = st.number_input("Active power (W)", 0.01, 5000.0, float(original_power))
                if _changed(original_power, values["active_power_w"]) and values.get("catalog_product"):
                    values["annual_energy_kwh"] = None
                    values["observed_energy"] = False
                    _mark_override(values, "energy", invalidated_observation=power_was_observed)
                original_repairability = values["repairability"]
                repairability_was_observed = bool(values.get("observed_repairability"))
                values["repairability"] = st.slider("Repairability", 0.0, 10.0, float(original_repairability), 0.1)
                if _changed(original_repairability, values["repairability"]) and values.get("catalog_product"):
                    values["observed_repairability"] = False
                    _mark_override(values, "repairability", invalidated_observation=repairability_was_observed)
                values["recyclability_pct"] = st.slider("Recyclability (%)", 0.0, 100.0, float(values["recyclability_pct"]), 1.0)
                values["recycled_content_pct"] = st.slider("Recycled content (%)", 0.0, 100.0, float(values["recycled_content_pct"]), 1.0)
                original_battery = values["battery_wh"]
                original_replaceable = bool(values["replaceable_battery"])
                battery_was_observed = bool(values.get("observed_battery"))
                values["battery_wh"] = st.number_input("Battery capacity (Wh)", 0.0, 2000.0, float(original_battery))
                values["replaceable_battery"] = st.checkbox("User-replaceable battery", original_replaceable)
                if (_changed(original_battery, values["battery_wh"]) or original_replaceable != values["replaceable_battery"]) and values.get("catalog_product"):
                    values["observed_battery"] = False
                    _mark_override(values, "battery", invalidated_observation=battery_was_observed)
                values["weight_kg"] = st.number_input("Weight (kg)", 0.01, 500.0, float(values["weight_kg"]))
                values["transport_km"] = st.number_input("Transport distance (km)", 0.0, 50000.0, float(values["transport_km"]))

    if values is None:
        with dashboard:
            st.info("Choose a matching product to build the dashboard.")
    else:
        result = assess(values)
        with dashboard:
            product_evidence = has_product_environmental_evidence(values)
            if values.get("catalog_product") and product_evidence:
                st.markdown(
                    '<div class="callout"><b>Catalog-backed identity</b><br>This model name or identifier comes from a linked source. Individual lifecycle fields may still be estimated; see Evidence coverage below.</div>',
                    unsafe_allow_html=True,
                )
            elif values.get("catalog_product"):
                st.markdown(
                    '<div class="callout warning"><b>Verified identity · category estimate</b><br>The product name is source-backed, but no model-specific environmental measurement is bundled. The score uses editable category assumptions and wider uncertainty.</div>',
                    unsafe_allow_html=True,
                )
            else:
                estimate_scope = "category" if values.get("category") != "Other" else "generic electronics"
                st.markdown(
                    f'<div class="callout warning"><b>{html.escape(estimate_scope.title())} estimate · no verified model record</b><br>The name is user supplied and the result uses editable {html.escape(estimate_scope)} assumptions. It is not a measured footprint for this exact device.</div>',
                    unsafe_allow_html=True,
                )
            st.caption(f"{values['manufacturer']}  /  {values['category']}  /  {values.get('model_number', '')}")
            st.header(values["name"])
            score_color = "#2fd484" if result.eco_score >= 75 else "#63bd75" if result.eco_score >= 55 else "#e2a542" if result.eco_score >= 35 else "#e06c5d"
            score_label = impact_category(result.eco_score) if product_evidence else "Category estimate" if values.get("category") != "Other" else "Generic estimate"
            score_left, score_right = st.columns([1.02, 2.15], gap="medium")
            with score_left:
                st.markdown(
                    f"""
                    <div class="score-card"><div class="score-ring" style="--ring-target:{result.eco_score * 3.6:.1f}deg;--ring-color:{score_color}"><div class="score-value">{result.eco_score}<br><small>/ 100</small></div></div>
                    <div class="score-copy"><b>Eco score</b><span>Likely {result.score_low:.0f}–{result.score_high:.0f}</span><span class="score-chip">{score_label}</span></div></div>
                    """,
                    unsafe_allow_html=True,
                )
            with score_right:
                lifecycle_label = "Reported lifecycle" if values.get("observed_carbon") else "Lifecycle scenario"
                energy_state = "Published annual value" if values.get("observed_energy") and not _missing(values.get("annual_energy_kwh")) else "Usage scenario"
                st.markdown(
                    f"""
                    <div class="kpi-grid">
                      <div class="kpi"><span class="kpi-label">{lifecycle_label}</span><span class="kpi-value">{result.lifecycle_carbon:,.0f} <small>kg CO₂e</small></span><span class="kpi-delta">Range ± {result.uncertainty:,.0f}</span></div>
                      <div class="kpi"><span class="kpi-label">Annual energy</span><span class="kpi-value">{result.annual_energy:,.1f} <small>kWh</small></span><span class="kpi-delta">{energy_state}</span></div>
                      <div class="kpi"><span class="kpi-label">Data confidence</span><span class="kpi-value">{result.confidence}<small>%</small></span><span class="kpi-delta">Evidence coverage, not probability</span></div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            narrative = explanation(values, result)
            st.markdown(f'<div class="callout"><b>AI-assisted explanation</b><br>{html.escape(narrative)}</div>', unsafe_allow_html=True)
            st.markdown("### Lifecycle pressure")
            st.altair_chart(factor_chart(result), use_container_width=True, theme=None)
            evidence_column, choices_column = st.columns([1.05, 1], gap="large")
            with evidence_column:
                st.markdown("### Evidence coverage")
                for field, state in provenance(values):
                    css_class = "observed" if state == "Observed" else "estimated"
                    st.markdown(f'<div class="field-row"><span>{field}</span><span class="{css_class}">{state}</span></div>', unsafe_allow_html=True)
                if not _missing(values.get("repair_attempts")):
                    st.caption(f"Open Repair brand/category profile: {int(values['repair_attempts']):,} attempts · {float(values.get('brand_repair_success_rate', 0)):.0%} fixed. This is not model-specific evidence.")
                if values.get("source_url"):
                    st.link_button(f"Open {values['source_name']} source ↗", values["source_url"])
            with choices_column:
                st.markdown("### Better choices")
                tips = recommendations(values, result)
                for tip in tips:
                    st.write(f"✓ {tip}")
                peers = best_peers(values) if product_evidence else []
                if peers:
                    st.caption("Higher modeled peers, prioritising the strongest comparable source field")
                    for peer_values, peer_result in peers:
                        st.markdown(f"**{peer_values['manufacturer']} {peer_values['name']}** · score {peer_result.eco_score:.0f} · {peer_values['source_name']}")

            action_1, action_2, action_3 = st.columns([0.8, 1.05, 1.25])
            current_saved = saved_record(values, result)
            with action_1:
                if st.button("Save to shortlist", type="primary", width="stretch"):
                    st.session_state.saved_gadgets = [item for item in st.session_state.saved_gadgets if item["id"] != current_saved["id"]] + [current_saved]
                    st.toast("Saved to your shortlist")
            with action_2:
                pdf = assessment_pdf(values, result, narrative, tips)
                st.download_button("Download impact report", pdf, f"luma-{_safe_name(values['name'])}.pdf", "application/pdf", width="stretch")
            with action_3:
                shareable_product_id = str(values.get("product_id", "")).strip()
                if shareable_product_id:
                    share_query = f"?theme={THEME}&tab=analyse&product={quote(shareable_product_id)}&region={quote(region if 'region' in locals() else 'India')}"
                    share_url = f"{st.context.url}{share_query}"
                    st.link_button("Open share link ↗", share_url, width="stretch")
                    st.caption("Keeps this catalogue gadget, region and theme.")
                else:
                    st.caption("Custom scenarios cannot be reopened from a link yet. Save or download this assessment instead.")
            if shareable_product_id:
                with st.expander("Copy a link to this assessment"):
                    st.caption("Send this full URL to reopen the same catalogue assessment.")
                    st.code(share_url, language=None, wrap_lines=True)


with compare:
    st.subheader("Compare devices")
    st.caption("Search globally, then apply the same regional grid and declared assumptions across every selected record.")
    c1, c2 = st.columns([1, 1.4])
    with c1:
        compare_category = st.selectbox("Product category", ["All categories", *sorted(catalog["category"].unique())], key="compare_category")
    with c2:
        compare_query = st.text_input("Search candidates", placeholder="iPhone, MacBook, Galaxy, model number…", key="compare_query")
    compare_categories = None if compare_category == "All categories" else [compare_category]
    compare_all = search_catalog(compare_query, compare_categories, None, primary_only=True) if compare_query.strip() or compare_categories else catalog.iloc[0:0]
    compare_pool = compare_all.head(1000)
    if len(compare_all) > len(compare_pool):
        st.caption(f"{len(compare_all):,} candidates match; showing the top {len(compare_pool):,}. Add a model or manufacturer to refine the list.")
    compare_labels = [(product_label(row), index) for index, row in compare_pool.iterrows()]
    chosen = st.multiselect("Select 2–5 records", [item[0] for item in compare_labels], max_selections=5, key="compare_selected")
    if len(chosen) >= 2:
        label_lookup = dict(compare_labels)
        comparison_records, report_records = [], []
        compared_categories: set[str] = set()
        default_grid = float(grid_data[grid_data["region"].eq("India")]["kg_co2e_per_kwh"].iloc[-1]) if "India" in set(grid_data["region"]) else 0.42
        for label in chosen:
            compared_values = product_values(catalog.loc[label_lookup[label]])
            compared_categories.add(str(compared_values["category"]))
            compared_values["grid_kg_co2_per_kwh"] = default_grid
            compared_result = assess(compared_values)
            comparison_records.append(
                {
                    "Product": f"{compared_values['manufacturer']} {compared_values['name']}", "Eco score": compared_result.eco_score,
                    "Likely low": compared_result.score_low, "Likely high": compared_result.score_high,
                    "Lifecycle kg CO₂e": compared_result.lifecycle_carbon, "Annual energy kWh": compared_result.annual_energy,
                    "Repairability": compared_values["repairability"], "Confidence": compared_result.confidence, "Source": compared_values["source_name"],
                }
            )
            report_records.append(saved_record(compared_values, compared_result))
        comparison = pd.DataFrame(comparison_records).set_index("Product").sort_values("Eco score", ascending=False)
        if len(compared_categories) > 1:
            st.warning("These devices serve different purposes. Compare the factor breakdowns, but do not treat the score order as a like-for-like buying recommendation.")
        render_data_table(comparison.reset_index(), height=280, min_width=980, aria_label="Gadget comparison")
        chart_data = comparison.reset_index()
        compare_chart = (
            alt.Chart(chart_data)
            .mark_bar(cornerRadiusEnd=7)
            .encode(
                x=alt.X("Eco score:Q", scale=alt.Scale(domain=[0, 100])),
                y=alt.Y("Product:N", sort="-x", title=None),
                color=alt.Color("Eco score:Q", scale=alt.Scale(domain=[0, 100], range=["#e1a84e", "#4cdd93"]), legend=None),
                tooltip=["Product", "Eco score", "Confidence"],
            )
            .properties(height=max(150, len(chart_data) * 46))
            .configure(background=palette["card"])
            .configure_view(strokeWidth=0)
            .configure_axis(labelColor=palette["muted"], titleColor=palette["muted"], gridColor=palette["line"], domain=False)
        )
        st.altair_chart(compare_chart, use_container_width=True, theme=None)
        st.info(f"Highest modeled score here: **{comparison.index[0]}**. Overlapping score ranges mean the order is not conclusive.")
        st.download_button("Download comparison PDF", comparison_pdf(report_records), "luma-comparison.pdf", "application/pdf")
    else:
        st.caption("Choose at least two records. Search by a family, brand or identifier to narrow the list.")


with saved:
    st.subheader("Your shortlist and reports")
    if not st.session_state.saved_gadgets:
        st.info("Your shortlist is empty. Save a gadget from the analysis dashboard to build a portable comparison.")
    else:
        shortlist = pd.DataFrame(st.session_state.saved_gadgets)
        for item in st.session_state.saved_gadgets:
            st.markdown(f'<div class="saved-card"><strong>{html.escape(item["product"])}</strong><br><span>{html.escape(item["category"])} · eco score {item["eco_score"]:.1f} ({item["score_low"]:.0f}–{item["score_high"]:.0f}) · confidence {item["confidence"]}% · {html.escape(item["source"])}</span></div>', unsafe_allow_html=True)
        table_columns = ["product", "category", "eco_score", "score_low", "score_high", "lifecycle_carbon", "annual_energy", "confidence", "source"]
        shortlist_view = shortlist[table_columns].rename(
            columns={
                "product": "Product", "category": "Category", "eco_score": "Eco score",
                "score_low": "Likely low", "score_high": "Likely high",
                "lifecycle_carbon": "Lifecycle kg CO₂e", "annual_energy": "Annual energy kWh",
                "confidence": "Confidence", "source": "Source",
            }
        )
        render_data_table(shortlist_view, height=300, min_width=980, aria_label="Saved gadget shortlist")
        s1, s2, s3 = st.columns(3)
        with s1:
            st.download_button("Download shortlist CSV", shortlist.to_csv(index=False).encode(), "luma-shortlist.csv", "text/csv", width="stretch")
        with s2:
            st.download_button("Download shortlist PDF", comparison_pdf(st.session_state.saved_gadgets), "luma-shortlist.pdf", "application/pdf", width="stretch")
        with s3:
            if st.button("Clear shortlist", width="stretch"):
                st.session_state.saved_gadgets = []
                st.rerun()


with intelligence:
    st.subheader("AI, evidence and data quality")
    blended = model_metrics.get("blended", {})
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Catalog records", f"{len(catalog):,}")
    q2.metric("Model scenarios", f"{model_metrics.get('samples', 0):,}")
    q3.metric("Holdout MAE", f"{blended.get('mae', 0):.2f} pts")
    q4.metric("Specialists", len(model_metrics.get("categories", {})))
    st.markdown(
        """
        <div class="method-flow">
          <div class="flow-node">Public evidence<span>Registries, certification, repair and product reports</span></div><div class="flow-arrow">→</div>
          <div class="flow-node">Transparent lifecycle ledger<span>72–100% depending on evidence</span></div><div class="flow-arrow">+</div>
          <div class="flow-node">Category-aware neural estimate<span>Up to 28%, reduced without product observations</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        """The global neural network learns broad nonlinear relationships, while specialist networks learn within-category patterns for phones, laptops, tablets, TVs and other device families. Training features are anchored to this snapshot's observed product distributions. The target remains a **disclosed physics-informed lifecycle ledger**, because no public dataset provides complete, comparable LCAs for every gadget.

For catalog products with at least one environmental observation, the neural estimate contributes at most 28%. It drops to 12% for identity-only or unlisted records in a recognised category and to zero for generic ``Other`` devices. The score range combines model holdout error with evidence coverage and is widened for weakly evidenced devices. It is a sensitivity range—not a guarantee or regulatory declaration."""
    )
    d1, d2 = st.columns([1, 1])
    with d1:
        st.markdown("#### Snapshot quality")
        quality_counts = catalog["data_quality"].fillna("Unclassified").value_counts().to_dict()
        freshness_counts = catalog["freshness_status"].fillna("unknown").value_counts().to_dict()
        quality_frame = pd.DataFrame([{"Level": key, "Records": value} for key, value in quality_counts.items()])
        freshness_frame = pd.DataFrame([{"Freshness": key, "Records": value} for key, value in freshness_counts.items()])
        render_data_table(quality_frame, height=220, min_width=420, aria_label="Data quality summary")
        render_data_table(freshness_frame, height=220, min_width=420, aria_label="Data freshness summary")
        st.caption(f"Schema {metadata.get('schema_version')} · snapshot {metadata.get('snapshot_id')} · {metadata.get('duplicate_evidence_records', 0):,} records belong to multi-source evidence groups")
    with d2:
        st.markdown("#### Model calibration")
        category_rows = []
        for category, metrics in model_metrics.get("categories", {}).items():
            category_rows.append({"Category": category, "MAE": metrics.get("mae"), "P90 error": metrics.get("p90_absolute_error"), "R²": metrics.get("r2")})
        calibration_frame = pd.DataFrame(category_rows).sort_values("Category") if category_rows else pd.DataFrame()
        render_data_table(calibration_frame, height=300, min_width=560, aria_label="Model calibration metrics")
    st.markdown("#### Current sources")
    for source in metadata.get("sources", []):
        st.markdown(
            f'<div class="source-card"><a href="{html.escape(source["url"])}" target="_blank">{html.escape(source["name"])} ↗</a><span>{source.get("records", 0):,} relevant records · retrieved {html.escape(source.get("retrieved_at", "")[:10])} · {html.escape(source.get("license", "See source terms"))}</span></div>',
            unsafe_allow_html=True,
        )
    st.markdown(
        f'<div class="source-card"><a href="https://github.com/AryaPriyanshu/environmental-impact-analyzer" target="_blank">Reviewed consumer identity manifest ↗</a><span>{max(0, len(catalog) - metadata.get("record_count", len(catalog))):,} identity or corrected-variant records · reviewed 2026-08-24 · manufacturer pages linked; source terms apply</span></div>',
        unsafe_allow_html=True,
    )
    with st.expander("Limitations and responsible interpretation"):
        st.markdown(
            """
            - Most official product records publish one part of the lifecycle, not a complete LCA.
            - Any device name can be assessed, but an absent model receives a clearly marked category or generic estimate—not invented product measurements.
            - Manufacturer footprints use report-specific configurations, geography, boundaries and assumptions.
            - Open Repair statistics are aggregated by brand and category; they are never presented as model-specific outcomes.
            - Electricity intensity changes over time and does not represent marginal electricity or every local tariff.
            - Model metrics measure recovery of the declared scenario target, not agreement with unknown real-world truth.
            - Use verified EPDs, supplier data and ISO 14040/14044 methods for procurement or regulatory decisions.
            """
        )
