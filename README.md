# 🌍 Environmental Impact Analyzer

A machine learning-powered web application that analyzes environmental indicators and predicts an overall environmental impact score through an interactive dashboard.

## 📌 Overview

The Environmental Impact Analyzer demonstrates how machine learning can be used to combine multiple environmental indicators into a single predicted impact score.

Users can adjust environmental variables through an interactive Streamlit interface and instantly see how those values affect the predicted environmental impact.

The project includes data generation, preprocessing, model training, evaluation, prediction, visualization, and an interactive web interface.

## ✨ Features

- Interactive environmental indicator controls
- Real-time environmental impact prediction
- Impact score from 0–100
- Automatic Low, Moderate, and High impact classification
- Visual comparison of environmental indicators
- Dynamic recommendations based on input values
- Machine learning model trained on synthetic environmental data
- Clean Streamlit dashboard interface

## 🌱 Environmental Indicators

The model analyzes six environmental factors:

- CO₂ Emissions
- Energy Consumption
- Water Usage
- Waste Generated
- Renewable Energy Percentage
- Recycling Rate

Higher renewable energy usage and recycling rates contribute positively, while increased emissions, energy consumption, water usage, and waste contribute to environmental impact.

## 🤖 Machine Learning

The project uses a regression-based machine learning pipeline to estimate environmental impact.

The workflow includes:

1. Generating synthetic environmental data
2. Preparing input features
3. Splitting data into training and testing sets
4. Scaling features for model training
5. Training the prediction model
6. Evaluating model performance
7. Saving the trained model and scaler
8. Loading the model into the Streamlit application for real-time predictions

### Model Performance

During the current training run:

- **Training samples:** 2,000
- **Testing samples:** 500
- **Mean Absolute Error (MAE):** 2.02
- **R² Score:** 0.966

These results are based on synthetic data generated specifically for this portfolio project.

## 🛠️ Technologies Used

- Python
- Streamlit
- Pandas
- NumPy
- Scikit-learn
- Joblib
- Matplotlib
- Git
- GitHub

## 📂 Project Structure

```text
environmental-impact-analyzer/
│
├── app.py
├── requirements.txt
├── README.md
├── LICENSE
│
├── data/
│   └── environmental_data.csv
│
├── models/
│   ├── environmental_model.pkl
│   └── scaler.pkl
│
└── src/
    ├── train_model.py
    └── utils.py
```

## 🚀 Running the Project Locally

Clone the repository:

```bash
git clone https://github.com/AryaPriyanshu/environmental-impact-analyzer.git
cd environmental-impact-analyzer
```

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

Run the application:

```bash
streamlit run app.py
```

Streamlit will provide a local address that can be opened in your browser.

## 💡 Example Usage

Adjust the environmental indicators using the controls in the sidebar.

The application processes the selected values and displays:

- Predicted environmental impact score
- Environmental impact category
- Relative indicator visualization
- Recommendations based on the selected environmental conditions

This allows users to experiment with different environmental scenarios and observe how changing individual indicators affects the predicted result.

## 🎯 Project Purpose

I built this project to strengthen my practical understanding of:

- Machine learning workflows
- Regression modeling
- Data preprocessing
- Model evaluation
- Python application development
- Interactive data visualization
- Deploying trained models inside user-facing applications

## ⚠️ Disclaimer

This is a portfolio and educational project. The model is trained on synthetically generated environmental data and its predictions should not be interpreted as professional environmental assessments or scientific measurements.

## 👨‍💻 Author

**Priyanshu Arya**

MCA Graduate | Python | Artificial Intelligence | Software Development

[LinkedIn](https://www.linkedin.com/in/priyanshu-arya-408a10268/)