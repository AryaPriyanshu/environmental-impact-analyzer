import joblib
import pandas as pd
import streamlit as st

from src.utils import impact_category, recommendations


st.set_page_config(
    page_title="Environmental Impact Analyzer",
    page_icon="🌍",
    layout="wide",
)

st.title("🌍 Environmental Impact Analyzer")
st.write(
    "Analyze environmental indicators and estimate an overall impact score "
    "using a neural-network regression model."
)

try:
    model = joblib.load("models/environmental_model.pkl")
    scaler = joblib.load("models/scaler.pkl")
except FileNotFoundError:
    st.error("Model not found. Run `python src/train_model.py` first.")
    st.stop()

st.sidebar.header("Environmental Indicators")

co2 = st.sidebar.slider("CO₂ Emissions", 50, 1000, 400)
energy = st.sidebar.slider("Energy Consumption", 100, 5000, 1800)
water = st.sidebar.slider("Water Usage", 50, 3000, 1000)
waste = st.sidebar.slider("Waste Generated", 10, 1000, 300)
renewable = st.sidebar.slider("Renewable Energy (%)", 0, 100, 40)
recycling = st.sidebar.slider("Recycling Rate (%)", 0, 100, 50)

values = {
    "co2_emissions": co2,
    "energy_consumption": energy,
    "water_usage": water,
    "waste_generated": waste,
    "renewable_energy": renewable,
    "recycling_rate": recycling,
}

input_data = pd.DataFrame([values])
scaled_data = scaler.transform(input_data)
score = float(model.predict(scaled_data)[0])
score = max(0.0, min(100.0, score))
category = impact_category(score)

col1, col2 = st.columns(2)

with col1:
    st.subheader("Predicted Environmental Impact")
    st.metric("Impact Score", f"{score:.1f} / 100")
    st.metric("Impact Level", category)
    st.progress(int(score))

with col2:
    st.subheader("Current Indicators")
    chart_data = pd.DataFrame(
        {
            "Indicator": [
                "CO₂",
                "Energy",
                "Water",
                "Waste",
                "Renewable",
                "Recycling",
            ],
            "Relative Level": [
                co2 / 1000 * 100,
                energy / 5000 * 100,
                water / 3000 * 100,
                waste / 1000 * 100,
                renewable,
                recycling,
            ],
        }
    ).set_index("Indicator")

    st.bar_chart(chart_data)

st.subheader("💡 Recommendations")

for tip in recommendations(values):
    st.write(f"• {tip}")

st.divider()
st.caption(
    "Portfolio demonstration project. Predictions are generated from a model "
    "trained on synthetic environmental data and should not be interpreted as "
    "professional environmental assessments."
)
