import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent.parent / "components"))

import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, date, timedelta
import io
from sortable_katalogi_comp import sortable_katalogi as _sortable_katalogi
from utils import list_plans, load_plan, load_plan_zonki, save_plan, overwrite_plan, load_config
from planning.engine import przypisz_maszyny, planuj, reorder_katalogi

st.set_page_config(page_title="Planner", page_icon="📅", layout="wide")
st.title("📅 Planner produkcji")
st.markdown("---")

# ── RAL palette ────────────────────────────────────────────────────────────────
RAL_HEX = {
    "3009": "#5E2129",
    "3011": "#781122",
    "6020": "#3B7A57",
    "7016": "#373F43",
    "8004": "#8D3D2B",
    "8017": "#45322E",
    "8019": "#403A3A",
    "9005": "#1C1C1C",
    "9007": "#8C8C8C",
    "NDAB": "#D4C89A",
    "ZDAB": "#C8C0A0",
    "ALC":  "#B0B7BC",
}
SETUP_COLOR    = "#FFB3BA"
TRANSIT_COLOR  = "#FFAA5A"
DEFAULT_COLOR  = "#7B8B9A"
WEW_COLOR      = "#F4A6C0"  # różowy dla zleceń wewnętrznych
CRANE_CONFLICT_COLOR = "#CC0000"  # krwistoczerwony — maszyna czeka na suwnicę
MOCK_DATE     = date(2026, 4, 28)

def ral_hex(code: str) -> str:
    return RAL_HEX.get(str(code).upper(), DEFAULT_COLOR)

def ral_hex_filc(code: str) -> str:
    """45% jaśniejszy odcień tego samego RAL — dla zleceń z Filcem."""
    h = ral_hex(code)
    def _l(c): return min(255, int(int(h[c:c+2], 16) + (255 - int(h[c:c+2], 16)) * 0.45))
    return f"#{_l(1):02x}{_l(3):02x}{_l(5):02x}"

def mins_to_dt(minutes: float) -> datetime:
    return datetime(MOCK_DATE.year, MOCK_DATE.month, MOCK_DATE.day) + timedelta(seconds=int(minutes * 60))

# ── Mock data ──────────────────────────────────────────────────────────────────
@st.cache_data
def build_mock() -> pd.DataFrame:
    machines = [
        {"name": "S2-T20P",  "speed": 16.2, "pg": "TRAPEZY"},
        {"name": "S2-T18P2", "speed": 16.4, "pg": "TRAPEZY"},
        {"name": "S2-T18H",  "speed": 15.8, "pg": "TRAPEZY"},
        {"name": "Z2-T20P",  "speed": 17.3, "pg": "TRAPEZY"},
        {"name": "Z2-T18P",  "speed": 18.6, "pg": "TRAPEZY"},
    ]
    plan = {
        "S2-T20P": [
            ("T20PM8017AM50F125D", "8017", [
                ("ZP-15407/26/SEKO", 43.2,  "2026-04-26"),
                ("ZP-15408/26/SEKO", 128.5, "2026-04-26"),
                ("ZP-15409/26/SEKO", 87.3,  "2026-05-02"),
                ("ZP-15410/26/SEKO", 156.8, "2026-04-30"),
            ]),
            ("T20PM7016MA50F125D", "7016", [
                ("ZP-15420/26/SEKO", 95.6,  "2026-04-29"),
                ("ZP-15421/26/SEKO", 212.8, "2026-04-30"),
                ("ZP-15422/26/SEKO", 67.4,  "2026-05-01"),
            ]),
            ("T20PM8019IN50F125D", "8019", [
                ("ZP-15435/26/SEKO", 156.4, "2026-05-01"),
                ("ZP-15436/26/SEKO", 89.2,  "2026-05-01"),
                ("ZP-15437/26/SEKO", 73.1,  "2026-05-02"),
            ]),
            ("T20PM9005IN50F125D", "9005", [
                ("ZP-15450/26/SEKO", 234.7, "2026-05-03"),
                ("ZP-15451/26/SEKO", 67.8,  "2026-05-03"),
                ("ZP-15452/26/SEKO", 112.3, "2026-05-02"),
            ]),
        ],
        "S2-T18P2": [
            ("T18PM8017AM50125D", "8017", [
                ("ZP-15071/26/SEKO", 41.4,  "2026-04-29"),
                ("ZP-15072/26/SEKO", 98.7,  "2026-04-30"),
                ("ZP-15073/26/SEKO", 145.2, "2026-04-29"),
                ("ZP-15074/26/SEKO", 89.6,  "2026-04-30"),
            ]),
            ("T18PM7016MA50125D", "7016", [
                ("ZP-15080/26/SEKO", 178.5, "2026-04-30"),
                ("ZP-15081/26/SEKO", 67.9,  "2026-04-30"),
                ("ZP-15082/26/SEKO", 234.1, "2026-05-01"),
            ]),
            ("T18PM9007RA50125D", "9007", [
                ("ZP-15090/26/SEKO", 89.3,  "2026-05-01"),
                ("ZP-15091/26/SEKO", 145.7, "2026-05-02"),
                ("ZP-15092/26/SEKO", 67.8,  "2026-05-01"),
            ]),
            ("T18PM3009RA50125D", "3009", [
                ("ZP-15100/26/SEKO", 112.4, "2026-05-02"),
                ("ZP-15101/26/SEKO", 87.6,  "2026-05-02"),
            ]),
        ],
        "S2-T18H": [
            ("T18HM8019IN50F125D", "8019", [
                ("ZP-14927/26/SEKO", 27.6,  "2026-05-01"),
                ("ZP-14928/26/SEKO", 89.4,  "2026-04-30"),
                ("ZP-14929/26/SEKO", 134.2, "2026-04-30"),
                ("ZP-14930/26/SEKO", 78.5,  "2026-04-30"),
            ]),
            ("T18HM7016IN50F125D", "7016", [
                ("ZP-14940/26/SEKO", 178.3, "2026-05-01"),
                ("ZP-14941/26/SEKO", 56.7,  "2026-04-30"),
                ("ZP-14942/26/SEKO", 134.9, "2026-05-01"),
            ]),
            ("T18HM8017AM50F125D", "8017", [
                ("ZP-14950/26/SEKO", 98.5,  "2026-05-02"),
                ("ZP-14951/26/SEKO", 167.3, "2026-05-02"),
                ("ZP-14952/26/SEKO", 89.1,  "2026-05-01"),
            ]),
            ("T18HM9005IN50F125D", "9005", [
                ("ZP-14960/26/SEKO", 145.6, "2026-05-03"),
                ("ZP-14961/26/SEKO", 234.8, "2026-05-03"),
            ]),
        ],
        "Z2-T20P": [
            ("T20PM8017AM50F125D", "8017", [
                ("ZP-25101/26/ZABO", 56.3,  "2026-04-29"),
                ("ZP-25102/26/ZABO", 178.9, "2026-04-30"),
                ("ZP-25103/26/ZABO", 92.4,  "2026-04-29"),
            ]),
            ("T20PM7016MA50F125D", "7016", [
                ("ZP-25110/26/ZABO", 234.5, "2026-04-30"),
                ("ZP-25111/26/ZABO", 89.7,  "2026-04-30"),
                ("ZP-25112/26/ZABO", 145.2, "2026-05-01"),
            ]),
            ("T20PM8004RA50F125D", "8004", [
                ("ZP-25120/26/ZABO", 112.8, "2026-05-01"),
                ("ZP-25121/26/ZABO", 67.3,  "2026-05-02"),
                ("ZP-25122/26/ZABO", 198.6, "2026-05-01"),
            ]),
            ("T20PM6020RA50F125D", "6020", [
                ("ZP-25130/26/ZABO", 78.4,  "2026-05-02"),
                ("ZP-25131/26/ZABO", 156.7, "2026-05-02"),
                ("ZP-25132/26/ZABO", 89.3,  "2026-05-02"),
            ]),
        ],
        "Z2-T18P": [
            ("T18PM8017AM50125D", "8017", [
                ("ZP-25201/26/ZABO", 41.8,  "2026-04-26"),
                ("ZP-25202/26/ZABO", 134.5, "2026-04-26"),
                ("ZP-25203/26/ZABO", 89.3,  "2026-04-30"),
                ("ZP-25204/26/ZABO", 67.2,  "2026-04-29"),
            ]),
            ("T18PM8019IN50125D", "8019", [
                ("ZP-25210/26/ZABO", 178.4, "2026-04-30"),
                ("ZP-25211/26/ZABO", 92.7,  "2026-04-30"),
                ("ZP-25212/26/ZABO", 145.3, "2026-05-01"),
            ]),
            ("T18PM9005IN50125D", "9005", [
                ("ZP-25220/26/ZABO", 234.6, "2026-05-01"),
                ("ZP-25221/26/ZABO", 78.9,  "2026-05-01"),
            ]),
            ("T18PM7016MA50125D", "7016", [
                ("ZP-25230/26/ZABO", 156.4, "2026-05-02"),
                ("ZP-25231/26/ZABO", 89.8,  "2026-05-02"),
                ("ZP-25232/26/ZABO", 123.4, "2026-05-02"),
            ]),
        ],
    }

    rows = []
    for m in machines:
        cur      = 6 * 60.0
        limit    = 22 * 60.0
        prev_kat = None

        for kat, ral, orders in plan.get(m["name"], []):
            zp_mins   = [mb / m["speed"] for _, mb, _ in orders]
            setup_min = 12.0 if kat != prev_kat else 0.0
            total_kat = round(setup_min + sum(zp_mins), 1)

            if cur + setup_min > limit:
                break

            if kat != prev_kat:
                rows.append(dict(
                    Maszyna=m["name"], Twr_Profil_Glowny=m["pg"], Data=str(MOCK_DATE),
                    Start=mins_to_dt(cur), Koniec=mins_to_dt(cur + 12),
                    ZP="— Przezbrojenie —", Twr_Katalog=kat, RAL=ral,
                    MB=0.0, Minuty=12.0, LD="—",
                    Total_kat_min=total_kat, kolor_id="przezbrojenie",
                ))
                cur += 12.0
                prev_kat = kat

            for (zp, mb, ld), pm in zip(orders, zp_mins):
                if cur + pm > limit:
                    break
                _ld_d = date.fromisoformat(ld) if ld != "—" else None
                rows.append(dict(
                    Maszyna=m["name"], Twr_Profil_Glowny=m["pg"], Data=str(MOCK_DATE),
                    Start=mins_to_dt(cur), Koniec=mins_to_dt(cur + pm),
                    ZP=zp, Twr_Katalog=kat, RAL=ral,
                    MB=mb, Minuty=round(pm, 1), LD=ld,
                    Total_kat_min=total_kat, kolor_id=ral,
                    is_zonk=False,
                ))
                cur += pm

    return pd.DataFrame(rows)

# ── Uruchom planowanie ─────────────────────────────────────────────────────────
st.subheader("⚙️ Nowe planowanie")

dane   = st.session_state.get("dane")
zaklad = st.session_state.get("zaklad", "")

if dane is None:
    st.info("Najpierw wczytaj zlecenia na stronie **1 Import**.")
else:
    st.caption(f"Dane: **{len(dane)}** zleceń · zakład: **{zaklad}**")
    pc1, pc2, pc3, pc4 = st.columns([2, 1, 1, 1])
    p_start   = pc1.date_input("Data startowa", value=date.today())
    p_horizon = pc2.number_input("Horyzont (dni)", min_value=1, max_value=120, value=14)
    p_look    = pc3.number_input("Lookahead (dni)", min_value=0, max_value=30, value=3)
    pc4.markdown("<br>", unsafe_allow_html=True)
    run_plan  = pc4.button("🚀 Zaplanuj", type="primary", use_container_width=True)

    if run_plan:
        config    = load_config()
        maszyny   = config["machines"]
        kalendarz = config.get("calendar", {})
        operators = config.get("operators", [])
        shift_cfg = config.get("shift_config", {"A_first_shift_week": "2026-04-27"})
        op_vac    = config.get("operator_vacations", {})

        with st.spinner("Przypisuję maszyny…"):
            df_with_m, nieprzyp, konflikty = przypisz_maszyny(dane, maszyny, zaklad)

        if konflikty:
            for profil, ms in konflikty.items():
                st.error(f"Konflikt: Twr_Profil **{profil}** przypisany do {ms}. Popraw konfigurację maszyn.")
            st.stop()

        if nieprzyp:
            st.warning(
                f"Nie przypisano maszyny dla {len(nieprzyp)} profil(i): "
                f"{nieprzyp[:5]}{'…' if len(nieprzyp) > 5 else ''}"
            )

        if not operators:
            st.info("Brak operatorów — planuję bez uwzględnienia zmian (limit per maszyna).")

        with st.spinner("Planuję…"):
            gantt_df, raport = planuj(
                df_with_m, maszyny, kalendarz,
                operators=operators,
                shift_config=shift_cfg,
                operator_vacations=op_vac,
                data_start=p_start,
                horyzont=int(p_horizon),
                lookahead=int(p_look),
            )

        if gantt_df.empty:
            st.warning("Brak wyników — sprawdź dane i konfigurację maszyn.")
        else:
            label = f"{zaklad} | od {p_start} | h{int(p_horizon)}d L{int(p_look)}d"
            ts    = save_plan(gantt_df, label=label, zonki=raport.get("zonki", []))
            n_zonk = len(raport.get("zonki", []))
            st.success(
                f"✅ Plan zapisany: **{ts}** · "
                f"zaplanowano {raport['zaplanowane']} ZP · "
                f"niezaplanowanych {raport['niezaplanowane']} · "
                f"zonków {n_zonk}"
            )
            if raport["niezaplanowane_zp"]:
                with st.expander("📋 Niezaplanowane zlecenia"):
                    st.dataframe(
                        pd.DataFrame(raport["niezaplanowane_zp"]),
                        use_container_width=True, hide_index=True,
                    )
            st.rerun()

st.markdown("---")

# ── Plan selector ──────────────────────────────────────────────────────────────
plans = list_plans()

if not plans:
    # First run — save mock as initial plan
    mock_df = build_mock()
    ts = save_plan(mock_df, label="Plan przykładowy (mock)")
    st.info(f"Brak zapisanych planów — wygenerowano plan przykładowy ({ts}).")
    plans = list_plans()

def _fmt_ts(ts: str) -> str:
    # "2026-04-24T14-00-00" → "2026-04-24  14:00:00"
    date_part, time_part = ts.split("T")
    return f"{date_part}  {time_part.replace('-', ':')}"

plan_options = {p["timestamp"]: f"{_fmt_ts(p['timestamp'])}  —  {p['label']}" for p in plans}
sel_ts = st.selectbox(
    "📋 Wybierz plan",
    options=list(plan_options.keys()),
    format_func=lambda k: plan_options[k],
    index=0,
)
st.markdown("---")

df_all   = load_plan(sel_ts)
zonki_df = load_plan_zonki(sel_ts)

# ── Filters ────────────────────────────────────────────────────────────────────
col_f1, col_f2 = st.columns(2)
with col_f1:
    sel_date = st.selectbox("📅 Data", sorted(df_all["Data"].unique()))
with col_f2:
    pg_opts = ["Wszystkie"] + sorted(df_all["Twr_Profil_Glowny"].unique())
    sel_pg  = st.selectbox("🏷️ Twr_Profil_Glowny", pg_opts)

df = df_all[df_all["Data"] == sel_date].copy()
if sel_pg != "Wszystkie":
    df = df[df["Twr_Profil_Glowny"] == sel_pg]

st.caption(f"Segmentów: **{len(df)}** | Maszyn: **{df['Maszyna'].nunique()}**")
st.markdown("---")

# ── Zonki ──────────────────────────────────────────────────────────────────────
if not zonki_df.empty:
    _z_total_min = float(zonki_df["zonk_min"].sum())
    _z_total_mb  = float(zonki_df["zonk_MB"].sum())
    _z_total_zp  = int(zonki_df["zonk_ZP"].sum())
    _z_n         = len(zonki_df)

    _zc1, _zc2, _zc3, _zc4 = st.columns(4)
    _zc1.metric("🔴 Maszyno-dni z zonkiem", f"{_z_n}")
    _zc2.metric("⏱ Łącznie min poza 22:00", f"{_z_total_min:,.0f}")
    _zc3.metric("📦 MB w zonku",            f"{_z_total_mb:,.1f}")
    _zc4.metric("🔢 ZP w zonku",            f"{_z_total_zp}")

    with st.expander(f"📋 Szczegóły zonków ({_z_n} maszyno-dni)", expanded=True):
        st.dataframe(
            zonki_df.sort_values(["Data", "Maszyna"]),
            use_container_width=True, hide_index=True,
            column_config={
                "zonk_min": st.column_config.NumberColumn("Min poza 22:00", format="%.0f"),
                "zonk_MB":  st.column_config.NumberColumn("MB w zonku", format="%.1f"),
                "zonk_ZP":  st.column_config.NumberColumn("ZP w zonku", format="%d"),
            },
        )
else:
    st.success("✅ Brak zonków w tym planie.")

st.markdown("---")

# ── Gantt ──────────────────────────────────────────────────────────────────────
color_map = {}
for _kid in df["kolor_id"].unique():
    if _kid == "przezbrojenie":
        color_map[_kid] = SETUP_COLOR
    elif _kid == "przejscie":
        color_map[_kid] = TRANSIT_COLOR
    elif _kid == "konflikt_suwnicy":
        color_map[_kid] = CRANE_CONFLICT_COLOR
    elif _kid.endswith("_F"):
        color_map[_kid] = ral_hex_filc(_kid[:-2])
    else:
        color_map[_kid] = ral_hex(_kid)

_sel_d    = date.fromisoformat(sel_date)
day_start = datetime(_sel_d.year, _sel_d.month, _sel_d.day, 6,  0)
day_end   = datetime(_sel_d.year, _sel_d.month, _sel_d.day, 22, 0)

df_gantt = df.copy()
if "is_wew" in df_gantt.columns:
    _wew_mask = df_gantt["is_wew"] == True
    df_gantt.loc[_wew_mask, "kolor_id"] = "wew"
    color_map["wew"] = WEW_COLOR
if "is_zonk" in df_gantt.columns:
    _zonk_mask = df_gantt["is_zonk"] == True
    df_gantt.loc[_zonk_mask, "kolor_id"] = df_gantt.loc[_zonk_mask, "kolor_id"].astype(str) + "_Z"
    for _k in [k for k in list(color_map.keys()) if not k.endswith("_Z")]:
        color_map[_k + "_Z"] = color_map[_k]

# Rozszerz oś X jeśli zonki wystają poza 22:00
if not df_gantt.empty:
    _max_koniec = df_gantt["Koniec"].max()
    if pd.notna(_max_koniec) and _max_koniec > day_end:
        day_end = _max_koniec + pd.Timedelta(minutes=15)

if "Operator" not in df_gantt.columns:
    df_gantt["Operator"] = ""
if "Total_kat_MB" not in df_gantt.columns:
    df_gantt["Total_kat_MB"] = 0.0
df_gantt["Operator"] = df_gantt["Operator"].fillna("").astype(str)
df_gantt["Total_kat_MB"] = pd.to_numeric(df_gantt["Total_kat_MB"], errors="coerce").fillna(0.0)

fig = px.timeline(
    df_gantt.sort_values("Maszyna"),
    x_start="Start", x_end="Koniec", y="Maszyna",
    color="kolor_id", color_discrete_map=color_map,
    custom_data=["ZP", "Twr_Katalog", "RAL", "MB", "Minuty", "LD", "Total_kat_min", "Operator", "Total_kat_MB"],
)
fig.update_traces(
    hovertemplate=(
        "<b>%{customdata[0]}</b><br>"
        "──────────────────────<br>"
        "Operator: %{customdata[7]}<br>"
        "Katalog: %{customdata[1]}<br>"
        "Kolor RAL: %{customdata[2]}<br>"
        "Ilość: %{customdata[3]:.1f} MB<br>"
        "Czas ZP: %{customdata[4]:.1f} min<br>"
        "Batch katalogu: %{customdata[8]:.1f} MB / %{customdata[6]:.1f} min<br>"
        "Termin LD: %{customdata[5]}<br>"
        "<extra></extra>"
    ),
    marker_line_width=0.6,
    marker_line_color="rgba(0,0,0,0.25)",
)
fig.update_layout(
    xaxis=dict(
        range=[day_start, day_end],
        tickformat="%H:%M",
        dtick=3_600_000,
        title="Godzina",
        gridcolor="#E5E5E5",
    ),
    yaxis=dict(title="", autorange="reversed"),
    height=100 + df["Maszyna"].nunique() * 72,
    plot_bgcolor="white",
    paper_bgcolor="white",
    showlegend=True,
    legend_title_text="Kolor RAL",
    margin=dict(l=130, r=20, t=10, b=50),
    bargap=0.3,
)
for _tn in [k for k in color_map if k.endswith("_F") and not k.endswith("_F_Z")]:
    fig.update_traces(
        selector={"name": _tn},
        marker_line_width=2.5,
        marker_line_color="#C9A227",
    )
for _tn in [k for k in color_map if k.endswith("_Z")]:
    fig.update_traces(
        selector={"name": _tn},
        marker_line_width=3,
        marker_line_color="#cc0000",
    )
st.plotly_chart(fig, use_container_width=True)

# ── Legenda RAL ────────────────────────────────────────────────────────────────
with st.expander("🎨 Legenda kolorów"):
    items = [
        ("przezbrojenie", SETUP_COLOR, "⚙️ Przezbrojenie"),
        ("konflikt_suwnicy", CRANE_CONFLICT_COLOR, "🛑 Konflikt suwnicy"),
        ("wew", WEW_COLOR, "💗 Zlec. wewnętrzne"),
    ] + [
        (k,
         ral_hex(k[:-2] if k.endswith("_F") else k),
         f"RAL {k[:-2]} + Filc" if k.endswith("_F") else f"RAL {k}")
        for k in sorted(color_map)
        if k not in ("przezbrojenie", "przejscie", "wew", "konflikt_suwnicy")
        and not k.endswith("_Z")
    ]
    cols = st.columns(5)
    for i, (_, hex_c, label) in enumerate(items):
        txt_c = "#fff" if hex_c in ("#373F43","#45322E","#403A3A","#1C1C1C","#5E2129") else "#222"
        cols[i % 5].markdown(
            f'<div style="background:{hex_c};padding:6px 10px;border-radius:4px;'
            f'color:{txt_c};font-size:0.82em;margin-bottom:4px">{label}</div>',
            unsafe_allow_html=True,
        )

st.markdown("---")

# ── Zmień kolejność katalogów (DnD — pionowy timeline) ────────────────────────
st.subheader("🔀 Zmień kolejność katalogów")
st.caption("Przeciągnij kolorowe bloki aby zmienić kolejność katalogów, potem kliknij **Zastosuj**.")

def _hex_dark(h: str) -> bool:
    h = h.lstrip("#")
    r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
    return (.299*r + .587*g + .114*b)/255 < .5

_prod_df  = df[~df["kolor_id"].isin(["przezbrojenie","przejscie","konflikt_suwnicy"])].copy()
_setup_df = df[df["kolor_id"] == "przezbrojenie"].copy()
_filc_kats_all = set(df[df["kolor_id"].str.endswith("_F", na=False)]["Twr_Katalog"].unique())

_dnd_ss_key = f"dnd_{sel_date}"
_original_orders: dict[str, list[str]] = {}
_machines_data: list[dict] = []

for _maszyna in sorted(df["Maszyna"].unique()):
    _mp = _prod_df[_prod_df["Maszyna"] == _maszyna].sort_values("Start")
    _ms = _setup_df[_setup_df["Maszyna"] == _maszyna]
    _orig = list(dict.fromkeys(_mp["Twr_Katalog"].tolist()))
    if not _orig:
        continue
    _original_orders[_maszyna] = _orig
    _cur = st.session_state.get(_dnd_ss_key, {}).get(_maszyna, _orig)

    _st = _mp.groupby("Twr_Katalog").agg(
        mb=("MB","sum"), prod_min=("Minuty","sum"), ral=("RAL","first"), kid=("kolor_id","first")
    ).reset_index()
    _ss2 = _ms.groupby("Twr_Katalog").agg(setup_min=("Minuty","sum")).reset_index()
    _st = _st.merge(_ss2, on="Twr_Katalog", how="left").fillna(0)

    _items = []
    for _kat in _cur:
        _r = _st[_st["Twr_Katalog"] == _kat]
        if _r.empty:
            continue
        _r   = _r.iloc[0]
        _kid = str(_r["kid"])
        _col = color_map.get(_kid, DEFAULT_COLOR)
        _items.append({
            "katalog":   str(_kat),
            "color":     _col,
            "tc":        "#fff" if _hex_dark(_col) else "#222",
            "ral":       str(_r["ral"]),
            "mb":        float(_r["mb"]),
            "prod_min":  float(_r["prod_min"]),
            "setup_min": float(_r["setup_min"]),
            "filc":      str(_kat) in _filc_kats_all,
        })
    _machines_data.append({"name": _maszyna, "items": _items})

_comp_h = int((22 - 6) * 60 * 0.70) + 50  # 672 + 50

_dnd_result = _sortable_katalogi(
    machines=_machines_data,
    px_per_min=0.70,
    shift_start_hour=6,
    shift_break_hour=14,
    shift_end_hour=22,
    key=f"sortkat_{sel_date}",
    default=None,
    height=_comp_h,
)

if _dnd_result:
    _ss = st.session_state.get(_dnd_ss_key, {})
    for _mn, _no in _dnd_result.items():
        _ss[_mn] = _no
    st.session_state[_dnd_ss_key] = _ss

if st.button("✅ Zastosuj kolejność", key="dnd_apply", type="primary"):
    _cur_ss  = st.session_state.get(_dnd_ss_key, {})
    _cfg     = load_config()
    _upd_df  = df_all.copy()
    _changed = False
    for _mname, _orig in _original_orders.items():
        _new = _cur_ss.get(_mname, _orig)
        if _new != _orig:
            _m_cfg  = next((m for m in _cfg["machines"] if m.get("nazwa")==_mname or m.get("id")==_mname), {})
            _upd_df = reorder_katalogi(_upd_df, _mname, sel_date, _new, _m_cfg)
            _changed = True
    if _changed:
        st.session_state.pop(_dnd_ss_key, None)
        overwrite_plan(sel_ts, _upd_df)
        st.success("Kolejność katalogów zaktualizowana.")
        st.rerun()
    else:
        st.info("Kolejność bez zmian.")

st.markdown("---")

# ── Podsumowanie ───────────────────────────────────────────────────────────────
st.subheader("Podsumowanie dnia")
prod   = df[~df["kolor_id"].isin(["przezbrojenie", "przejscie", "konflikt_suwnicy"])]
setup  = df[df["kolor_id"] == "przezbrojenie"]
crane_conflict = df[df["kolor_id"] == "konflikt_suwnicy"]
summ   = prod.groupby("Maszyna").agg(ZP=("ZP","count"), MB=("MB","sum"), Prod_min=("Minuty","sum")).reset_index()
s_sum  = setup.groupby("Maszyna").agg(Setup_min=("Minuty","sum"), Przezbr=("ZP","count")).reset_index()
summ   = summ.merge(s_sum, on="Maszyna", how="left").fillna(0)
summ["Razem (min)"]  = (summ["Prod_min"] + summ["Setup_min"]).round(1)
summ["Wykorz. %"]    = (summ["Razem (min)"] / 960 * 100).round(1)
summ["MB"]           = summ["MB"].round(1)
summ["Prod_min"]     = summ["Prod_min"].round(1)
summ["Setup_min"]    = summ["Setup_min"].round(1)
summ["Przezbr"]      = summ["Przezbr"].astype(int)
filc_mb = (
    df[df["kolor_id"].str.endswith("_F", na=False)]
    .groupby("Maszyna")["MB"].sum()
    .reset_index(name="MB Filc")
)
summ = summ.merge(filc_mb, on="Maszyna", how="left").fillna(0)
summ["MB Filc"]      = summ["MB Filc"].round(1)
if "is_wew" in df.columns:
    _wew_mb = (
        prod[prod["is_wew"] == True]
        .groupby("Maszyna")["MB"].sum()
        .reset_index(name="MB wew")
    )
    summ = summ.merge(_wew_mb, on="Maszyna", how="left").fillna(0)
    summ["MB wew"] = summ["MB wew"].round(1)
else:
    summ["MB wew"] = 0.0

# Konflikt suwnicy per maszyna (mid-day idle czekający na suwnicę)
_conflict_per_m = (
    crane_conflict.groupby("Maszyna")["Minuty"].sum().reset_index(name="Konflikt suwnicy (min)")
    if not crane_conflict.empty
    else pd.DataFrame(columns=["Maszyna", "Konflikt suwnicy (min)"])
)
summ = summ.merge(_conflict_per_m, on="Maszyna", how="left").fillna(0)
summ["Konflikt suwnicy (min)"] = summ["Konflikt suwnicy (min)"].round(1)
if "is_zonk" in df.columns:
    _zonk_per_m = (
        df[df["is_zonk"] == True]
        .groupby("Maszyna")
        .agg(zonk_zp=("ZP","count"), zonk_min=("Minuty","sum"))
        .reset_index()
    )
    summ = summ.merge(_zonk_per_m, on="Maszyna", how="left").fillna(0)
    summ["zonk_zp"]  = summ["zonk_zp"].astype(int)
    summ["zonk_min"] = summ["zonk_min"].round(1)
    summ.columns = ["Maszyna","Liczba ZP","MB","Prod. (min)","Setup (min)","Przezbr.","Razem (min)","Wykorz. %","MB Filc","MB wew","Konflikt suwnicy (min)","Zonk (ZP)","Zonk (min)"]
else:
    summ.columns = ["Maszyna","Liczba ZP","MB","Prod. (min)","Setup (min)","Przezbr.","Razem (min)","Wykorz. %","MB Filc","MB wew","Konflikt suwnicy (min)"]
st.dataframe(summ, use_container_width=True, hide_index=True)

st.markdown("---")

# ── Excel export ───────────────────────────────────────────────────────────────
st.subheader("📥 Eksport do Excel")

def _build_detail(df_src: pd.DataFrame) -> pd.DataFrame:
    detail = df_src[~df_src["kolor_id"].isin(["przezbrojenie", "przejscie", "konflikt_suwnicy"])].copy()
    detail["Start"]  = detail["Start"].dt.strftime("%H:%M")
    detail["Koniec"] = detail["Koniec"].dt.strftime("%H:%M")
    return detail[["Maszyna","Data","ZP","Twr_Katalog","RAL","MB",
                    "Minuty","LD","Start","Koniec","Total_kat_min"]].rename(columns={
        "ZP": "Numer ZP", "RAL": "Kolor RAL",
        "Minuty": "Czas prod. (min)", "LD": "Termin LD",
        "Total_kat_min": "Łącznie katalog (min)",
    })

def _build_summ(df_src: pd.DataFrame) -> pd.DataFrame:
    prod  = df_src[~df_src["kolor_id"].isin(["przezbrojenie", "przejscie", "konflikt_suwnicy"])]
    setup = df_src[df_src["kolor_id"] == "przezbrojenie"]
    s = prod.groupby(["Data","Maszyna"]).agg(ZP=("ZP","count"), MB=("MB","sum"), Prod_min=("Minuty","sum")).reset_index()
    ss = setup.groupby(["Data","Maszyna"]).agg(Setup_min=("Minuty","sum"), Przezbr=("ZP","count")).reset_index()
    s = s.merge(ss, on=["Data","Maszyna"], how="left").fillna(0)
    s["Razem (min)"] = (s["Prod_min"] + s["Setup_min"]).round(1)
    s["Wykorz. %"]   = (s["Razem (min)"] / 960 * 100).round(1)
    s["MB"]          = s["MB"].round(1)
    s["Prod_min"]    = s["Prod_min"].round(1)
    s["Setup_min"]   = s["Setup_min"].round(1)
    s["Przezbr"]     = s["Przezbr"].astype(int)
    return s.rename(columns={
        "ZP": "Liczba ZP", "Prod_min": "Prod. (min)",
        "Setup_min": "Setup (min)", "Przezbr": "Przezbr.",
    }).sort_values(["Data","Maszyna"])

def to_excel(df_detail: pd.DataFrame, df_summ: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        _build_detail(df_detail).to_excel(writer, sheet_name="Plan szczegółowy", index=False)
        df_summ.to_excel(writer, sheet_name="Podsumowanie", index=False)
    return buf.getvalue()

_ecol1, _ecol2 = st.columns(2)
_ecol1.download_button(
    label="⬇️ Pobierz Excel (ten dzień)",
    data=to_excel(df, summ),
    file_name=f"plan_{sel_date}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
    use_container_width=True,
)
_ecol2.download_button(
    label="⬇️ Pobierz Excel (cały plan)",
    data=to_excel(df_all, _build_summ(df_all)),
    file_name=f"plan_total_{sel_ts}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="secondary",
    use_container_width=True,
)

def _build_qlik_csv(df_src: pd.DataFrame) -> bytes:
    prod = df_src[~df_src["kolor_id"].isin(["przezbrojenie", "przejscie", "konflikt_suwnicy"])]
    zp_list = sorted(prod["ZP"].dropna().unique())
    out = pd.DataFrame({"ZP": zp_list, "Zaplanowane": 1})
    return out.to_csv(index=False, sep=";").encode("utf-8")

st.download_button(
    label=f"⬇️ Eksport Qlik — ZP zaplanowane na {sel_date}",
    data=_build_qlik_csv(df),
    file_name=f"qlik_zp_{sel_date}.csv",
    mime="text/csv",
    use_container_width=True,
)
