import streamlit as st
import json
from datetime import datetime, date, timedelta
from urllib import request, parse, error
import math
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from src.theme import set_theme

# Use global theme (loads gradient / cards) with page-specific title & icon
set_theme(page_title="Weather Center", page_icon="🌤")

st.title("🌤 Weather & Forecast Center")
st.caption("Live conditions with hourly & daily insights (powered by Open-Meteo – no API key required)")

# ---------------------------------------------------------------------------------
# Utility & Caching
# ---------------------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=600)
def geocode_city(name: str):
    if not name:
        return []
    qs = parse.urlencode({"name": name, "count": 5, "language": "en", "format": "json"})
    url = f"https://geocoding-api.open-meteo.com/v1/search?{qs}"
    try:
        with request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("results", []) or []
    except Exception:
        return []


@st.cache_data(show_spinner=False, ttl=600)
def fetch_weather(lat: float, lon: float, timezone: str = "auto"):
    # Request daily + hourly metrics
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": timezone,
        "hourly": ",".join([
            "temperature_2m",
            "relativehumidity_2m",
            "precipitation_probability",
            "windspeed_10m",
            "winddirection_10m",
            "weathercode"
        ]),
        "daily": ",".join([
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "windspeed_10m_max",
            "winddirection_10m_dominant",
            "sunrise",
            "sunset",
            "uv_index_max"
        ]),
        "forecast_days": 14
    }
    qs = parse.urlencode(params)
    url = f"https://api.open-meteo.com/v1/forecast?{qs}"
    try:
        with request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except error.HTTPError as e:
        st.error(f"Weather API HTTP error: {e.code}")
    except Exception as e:
        st.error(f"Weather API error: {e}")
    return None


WEATHER_CODE_MAP = {
    0: ("Clear sky", "☀️"),
    1: ("Mainly clear", "🌤"),
    2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"),
    45: ("Fog", "🌫"),
    48: ("Rime fog", "🌫"),
    51: ("Light drizzle", "🌦"),
    53: ("Drizzle", "🌦"),
    55: ("Heavy drizzle", "🌧"),
    56: ("Freezing drizzle", "🌧"),
    57: ("Heavy freezing drizzle", "🌧"),
    61: ("Light rain", "🌦"),
    63: ("Rain", "🌧"),
    65: ("Heavy rain", "🌧"),
    66: ("Freezing rain", "🌧"),
    67: ("Heavy freezing rain", "🌧"),
    71: ("Light snow", "🌨"),
    73: ("Snow", "🌨"),
    75: ("Heavy snow", "❄️"),
    77: ("Snow grains", "❄️"),
    80: ("Light showers", "🌦"),
    81: ("Showers", "🌦"),
    82: ("Heavy showers", "⛈"),
    85: ("Light snow showers", "🌨"),
    86: ("Snow showers", "🌨"),
    95: ("Thunderstorm", "⛈"),
    96: ("Thunderstorm w/ hail", "⛈"),
    99: ("Severe thunderstorm w/ hail", "⛈"),
}


def c_to_f(c):
    return c * 9 / 5 + 32


# ---------------------------------------------------------------------------------
# Sidebar Controls
# ---------------------------------------------------------------------------------
with st.sidebar:
    st.header("🌍 Location")
    city_query = st.text_input("Search city", value="Dubai")
    unit = st.radio("Units", ["Metric (°C)", "Imperial (°F)"], horizontal=False)
    show_hourly = st.toggle("Show next 24h hourly chart", value=True)
    show_wind_rose = st.toggle("Show wind rose", value=True)
    show_uv = st.toggle("Show daily UV index", value=True)
    search_btn = st.button("🔎 Search / Refresh", use_container_width=True)


# Only geocode when user types or clicks; using key ensures re-run after button
if city_query:
    geo_results = geocode_city(city_query)
else:
    geo_results = []

if not geo_results:
    st.info("Enter a city name to fetch weather data.")
    st.stop()

# Let user select one if multiple
if len(geo_results) > 1:
    labels = [f"{g['name']}, {g.get('admin1','')} {g.get('country_code','')} (lat {g['latitude']:.2f}, lon {g['longitude']:.2f})" for g in geo_results]
    idx = st.selectbox("Multiple matches found", list(range(len(labels))), format_func=lambda i: labels[i])
    loc = geo_results[idx]
else:
    loc = geo_results[0]

lat = loc["latitude"]
lon = loc["longitude"]
timezone = loc.get("timezone", "auto")

st.subheader(f"📍 {loc['name']}, {loc.get('admin1','')} {loc.get('country_code','')}")
col_map, col_meta = st.columns([2,1])
with col_map:
    st.map(pd.DataFrame([{"lat": lat, "lon": lon}]), use_container_width=True)
with col_meta:
    st.markdown(f"**Latitude:** {lat:.3f}\n\n**Longitude:** {lon:.3f}\n\n**Timezone:** {timezone}")

data = fetch_weather(lat, lon, timezone)
if not data:
    st.stop()

# ---------------------------------------------------------------------------------
# Current Conditions
# ---------------------------------------------------------------------------------
current = data.get("current_weather", {})
current_code = int(current.get("weathercode", -1)) if current else -1
desc, icon = WEATHER_CODE_MAP.get(current_code, ("Unknown", "❓"))

current_time = current.get("time")
temp_c = current.get("temperature")
wind = current.get("windspeed")
wind_dir = current.get("winddirection")

if unit.startswith("Imperial") and temp_c is not None:
    temp_display = f"{c_to_f(temp_c):.1f} °F"
else:
    temp_display = f"{temp_c:.1f} °C" if temp_c is not None else "--"

st.markdown("---")
st.markdown(f"### {icon} Current Conditions – {datetime.fromisoformat(current_time).strftime('%Y-%m-%d %H:%M')}" if current_time else "### Current Conditions")
metric_cols = st.columns(5)
metric_cols[0].metric("Temperature", temp_display, help=desc)
metric_cols[1].metric("Wind (km/h)", f"{wind}" if wind is not None else "--", help=f"Direction {wind_dir}°")

# Daily data
daily = data.get("daily", {})

def convert_if_needed(vals):
    if unit.startswith("Imperial"):
        return [c_to_f(v) for v in vals]
    return vals

if daily:
    # Today's index 0
    tmax = daily.get("temperature_2m_max", [None])[0]
    tmin = daily.get("temperature_2m_min", [None])[0]
    if tmax is not None and tmin is not None:
        if unit.startswith("Imperial"):
            tmax, tmin = c_to_f(tmax), c_to_f(tmin)
        metric_cols[2].metric("High", f"{tmax:.1f} {'°F' if unit.startswith('Imperial') else '°C'}")
        metric_cols[3].metric("Low", f"{tmin:.1f} {'°F' if unit.startswith('Imperial') else '°C'}")

    uv = daily.get("uv_index_max", [None])[0]
    if uv is not None:
        metric_cols[4].metric("UV Index", f"{uv:.1f}")

# ---------------------------------------------------------------------------------
# Hourly 24h Chart
# ---------------------------------------------------------------------------------
hourly = data.get("hourly", {})
if show_hourly and hourly:
    st.markdown("#### Next 24 Hours")
    hours = [datetime.fromisoformat(t) for t in hourly.get("time", [])]
    now = datetime.fromisoformat(current_time) if current_time else datetime.utcnow()
    # Select next 24 hours (including current hour)
    df_hour = pd.DataFrame({
        "time": hours,
        "temp_c": hourly.get("temperature_2m", []),
        "precip_prob": hourly.get("precipitation_probability", []),
        "humidity": hourly.get("relativehumidity_2m", []),
        "windspeed": hourly.get("windspeed_10m", []),
    })
    df_hour = df_hour[df_hour["time"] >= now - timedelta(hours=1)].head(24)
    if unit.startswith("Imperial"):
        df_hour["temp"] = df_hour["temp_c"].apply(c_to_f)
        temp_label = "Temp (°F)"
    else:
        df_hour["temp"] = df_hour["temp_c"]
        temp_label = "Temp (°C)"

    fig = go.Figure()
    fig.add_trace(go.Bar(x=df_hour["time"], y=df_hour["precip_prob"], name="Precip %", marker_color="#6fa8dc", yaxis="y2", opacity=0.5))
    fig.add_trace(go.Scatter(x=df_hour["time"], y=df_hour["temp"], name=temp_label, mode="lines+markers", line=dict(color="#f39c12", width=3)))
    fig.update_layout(
        yaxis=dict(title=temp_label, side="left"),
        yaxis2=dict(title="Precip %", overlaying="y", side="right", range=[0,100]),
        margin=dict(l=40,r=40,t=30,b=30),
        legend=dict(orientation="h", y=-0.25),
        height=350,
        hovermode="x unified"
    )
    st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------------
# 10-Day Daily Forecast (Max/Min Temps)
# ---------------------------------------------------------------------------------
if daily:
    st.markdown("#### 10-Day Temperature & Precipitation Outlook")
    days = [datetime.fromisoformat(d) for d in daily.get("time", [])][:10]
    tmax = convert_if_needed(daily.get("temperature_2m_max", [])[:10])
    tmin = convert_if_needed(daily.get("temperature_2m_min", [])[:10])
    precip = daily.get("precipitation_sum", [])[:10]

    df_daily = pd.DataFrame({
        "date": days,
        "tmax": tmax,
        "tmin": tmin,
        "precip": precip,
        "wind_max": daily.get("windspeed_10m_max", [])[:10],
        "wind_dir": daily.get("winddirection_10m_dominant", [])[:10],
        "sunrise": daily.get("sunrise", [])[:10],
        "sunset": daily.get("sunset", [])[:10],
    })

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=df_daily["date"], y=df_daily["tmax"], name="High", line=dict(color="#e74c3c")))
    fig2.add_trace(go.Scatter(x=df_daily["date"], y=df_daily["tmin"], name="Low", line=dict(color="#3498db")))
    fig2.add_trace(go.Bar(x=df_daily["date"], y=df_daily["precip"], name="Precip Sum (mm)", marker_color="#95a5a6", opacity=0.4, yaxis="y2"))
    fig2.update_layout(
        yaxis=dict(title=f"Temperature ({'°F' if unit.startswith('Imperial') else '°C'})"),
        yaxis2=dict(title="Precip (mm)", overlaying="y", side="right"),
        barmode="overlay",
        legend=dict(orientation="h", y=-0.25),
        margin=dict(l=40,r=40,t=30,b=30),
        height=360,
    )
    st.plotly_chart(fig2, use_container_width=True)

    # Sunrise / Sunset duration chart (gantt-like)
    sun_df = []
    for i, row in df_daily.iterrows():
        try:
            sr = datetime.fromisoformat(row.sunrise)
            ss = datetime.fromisoformat(row.sunset)
            sun_df.append({"date": row.date.date(), "sunrise": sr, "sunset": ss, "duration_h": (ss - sr).seconds/3600})
        except Exception:
            continue
    if sun_df:
        st.markdown("#### Daylight Duration")
        sdf = pd.DataFrame(sun_df)
        fig_sun = go.Figure()
        for _, r in sdf.iterrows():
            fig_sun.add_trace(go.Bar(x=[r["duration_h"]], y=[r["date"].strftime('%b %d')], orientation='h', name=r["date"].strftime('%b %d'), hovertext=f"Sunrise {r['sunrise'].strftime('%H:%M')} - Sunset {r['sunset'].strftime('%H:%M')}", marker_color="#f1c40f"))
        fig_sun.update_layout(
            showlegend=False,
            xaxis=dict(title="Hours of daylight"),
            height=400,
            margin=dict(l=80,r=40,t=20,b=40)
        )
        st.plotly_chart(fig_sun, use_container_width=True)

# ---------------------------------------------------------------------------------
# Wind Rose (Daily Dominant Directions)
# ---------------------------------------------------------------------------------
if show_wind_rose and daily:
    dirs = daily.get("winddirection_10m_dominant", [])[:10]
    speeds = daily.get("windspeed_10m_max", [])[:10]
    if dirs and speeds:
        st.markdown("#### Wind Rose (Dominant Daily Direction)")
        # Convert to compass sector counts
        sectors = ["N","NE","E","SE","S","SW","W","NW"]
        def to_sector(deg):
            return sectors[int((deg % 360) / 45)]
        sec_counts = {}
        for d, s in zip(dirs, speeds):
            sec = to_sector(d)
            sec_counts.setdefault(sec, []).append(s)
        plot_df = pd.DataFrame({
            "sector": list(sec_counts.keys()),
            "speed": [sum(v)/len(v) for v in sec_counts.values()]
        })
        fig_wind = px.bar_polar(plot_df, r="speed", theta="sector", color="sector", color_discrete_sequence=px.colors.qualitative.Pastel)
        fig_wind.update_layout(margin=dict(l=30,r=30,t=30,b=30), legend=dict(orientation='h', y=-0.15))
        st.plotly_chart(fig_wind, use_container_width=True)

# ---------------------------------------------------------------------------------
# Optional UV Index Trend
# ---------------------------------------------------------------------------------
if show_uv and daily and daily.get("uv_index_max"):
    st.markdown("#### UV Index (Next 10 Days)")
    uv_df = pd.DataFrame({
        "date": [datetime.fromisoformat(d) for d in daily.get("time", [])][:10],
        "uv": daily.get("uv_index_max", [])[:10]
    })
    fig_uv = px.area(uv_df, x="date", y="uv", color_discrete_sequence=["#8e44ad"], title="")
    fig_uv.update_layout(margin=dict(l=40,r=40,t=10,b=40), height=260, yaxis=dict(range=[0, max(11, uv_df.uv.max()+1)], title="UV Index"))
    st.plotly_chart(fig_uv, use_container_width=True)

# ---------------------------------------------------------------------------------
# Raw / Debug Expander
# ---------------------------------------------------------------------------------
with st.expander("🔍 Raw API Data (debug)"):
    st.json({k: v for k, v in data.items() if k in ("current_weather", "daily")})

st.markdown("""
<style>
/* Mild glass card styling for metrics */
div[data-testid="stMetric"] {
  background: rgba(255,255,255,0.05);
  padding: 0.75rem 0.75rem 0.25rem 0.75rem;
  border-radius: 8px;
  backdrop-filter: blur(6px);
  border: 1px solid rgba(255,255,255,0.15);
}
</style>
""", unsafe_allow_html=True)

st.caption("Data: Open-Meteo (CC-BY 4.0). Page auto-caches results for 10 minutes.")
