import os
import joblib
import numpy as np
import pandas as pd

from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.neural_network import MLPRegressor
from sklearn.metrics import mean_absolute_error, r2_score


def generate_environmental_data(n_samples=2500, random_state=42):
    rng = np.random.default_rng(random_state)

    data = pd.DataFrame({
        "co2_emissions": rng.uniform(50, 1000, n_samples),
        "energy_consumption": rng.uniform(100, 5000, n_samples),
        "water_usage": rng.uniform(50, 3000, n_samples),
        "waste_generated": rng.uniform(10, 1000, n_samples),
        "renewable_energy": rng.uniform(0, 100, n_samples),
        "recycling_rate": rng.uniform(0, 100, n_samples),
    })

    impact = (
        0.35 * (data["co2_emissions"] / 1000)
        + 0.25 * (data["energy_consumption"] / 5000)
        + 0.15 * (data["water_usage"] / 3000)
        + 0.15 * (data["waste_generated"] / 1000)
        - 0.05 * (data["renewable_energy"] / 100)
        - 0.05 * (data["recycling_rate"] / 100)
    )

    noise = rng.normal(0, 0.025, n_samples)
    data["impact_score"] = np.clip((impact + noise) * 100, 0, 100)

    return data


def train_model():
    os.makedirs("data", exist_ok=True)
    os.makedirs("models", exist_ok=True)

    data = generate_environmental_data()
    data.to_csv("data/environmental_data.csv", index=False)

    X = data.drop(columns=["impact_score"])
    y = data["impact_score"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    model = MLPRegressor(
        hidden_layer_sizes=(64, 32),
        activation="relu",
        max_iter=1000,
        random_state=42,
        early_stopping=True,
    )

    model.fit(X_train_scaled, y_train)

    predictions = model.predict(X_test_scaled)

    mae = mean_absolute_error(y_test, predictions)
    r2 = r2_score(y_test, predictions)

    joblib.dump(model, "models/environmental_model.pkl")
    joblib.dump(scaler, "models/scaler.pkl")

    print("Environmental Impact Analyzer")
    print("--------------------------------")
    print(f"Training samples: {len(X_train)}")
    print(f"Testing samples:  {len(X_test)}")
    print(f"Mean Absolute Error: {mae:.2f}")
    print(f"R² Score: {r2:.3f}")
    print("\nModel saved successfully.")


if __name__ == "__main__":
    train_model()
