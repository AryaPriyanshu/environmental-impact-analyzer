def impact_category(score):
    if score < 30:
        return "Low"
    elif score < 60:
        return "Moderate"
    elif score < 80:
        return "High"
    return "Critical"


def recommendations(values):
    tips = []

    if values["co2_emissions"] > 600:
        tips.append("Reduce CO₂ emissions through cleaner transportation and energy sources.")

    if values["energy_consumption"] > 3000:
        tips.append("Improve energy efficiency and reduce unnecessary consumption.")

    if values["water_usage"] > 1800:
        tips.append("Introduce water-saving and recycling measures.")

    if values["waste_generated"] > 600:
        tips.append("Reduce waste generation and improve material reuse.")

    if values["renewable_energy"] < 40:
        tips.append("Increase the share of renewable energy.")

    if values["recycling_rate"] < 50:
        tips.append("Improve recycling and waste-separation practices.")

    if not tips:
        tips.append("Environmental indicators are relatively balanced. Continue monitoring them.")

    return tips
