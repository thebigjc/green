# Re-import necessary libraries due to execution state reset
import xml.etree.ElementTree as ET
from datetime import datetime
import pandas as pd
from pytz import timezone
import re

# Re-load the XML file due to code execution state reset
file_path = "./Hydro1_Electric_60_Minute_08-24-2023_12-14-2023.xml"
tree = ET.parse(file_path)
root = tree.getroot()

# Namespace used in the Green Button XML file
namespace = {"atom": "http://www.w3.org/2005/Atom", "espi": "http://naesb.org/espi"}


# Re-parse the ReadingType information due to execution state reset
def extract_reading_type_info(root):
    reading_type_info = {}
    reading_type = root.find(".//espi:ReadingType", namespace)
    if reading_type is not None:
        for child in reading_type:
            tag = re.sub(r"\{.*\}", "", child.tag)
            reading_type_info[tag] = child.text
    return reading_type_info


reading_type_info = extract_reading_type_info(root)

print(reading_type_info)


# Re-extract the usage data due to execution state reset
def extract_usage_data(root):
    usage_data = []
    for entry in root.findall("atom:entry", namespace):
        interval_block = entry.find(".//espi:IntervalBlock", namespace)
        if interval_block is not None:
            for interval_reading in interval_block.findall(
                "espi:IntervalReading", namespace
            ):
                time_period = interval_reading.find("espi:timePeriod", namespace)
                start = int(time_period.find("espi:start", namespace).text)
                duration = int(time_period.find("espi:duration", namespace).text)
                value = int(interval_reading.find("espi:value", namespace).text)
                start_time = datetime.utcfromtimestamp(start)
                usage_data.append(
                    {"start_time": start_time, "duration": duration, "value": value}
                )
    return usage_data


usage_data = extract_usage_data(root)
df_usage = pd.DataFrame(usage_data)


# Apply the powerOfTenMultiplier to adjust the usage values
df_usage["value_wh"] = df_usage["value"] * 10 ** int(
    reading_type_info["powerOfTenMultiplier"]
)
df_usage["adjusted_value_kWh"] = df_usage["value_wh"] / 1000

df_usage["hour_of_day"] = df_usage["start_time"].dt.hour
df_usage["day_of_week"] = df_usage["start_time"].dt.weekday
df_usage["date"] = df_usage["start_time"].dt.date
df_usage["is_weekend"] = df_usage["day_of_week"] >= 5


# Function to get the correct threshold based on the month
def get_tier_threshold(month):
    return (
        600 if 5 <= month <= 10 else 1000
    )  # Summer threshold is 600 kWh, winter threshold is 1000 kWh


def get_tou_period(month, hour, is_weekend):
    if is_weekend:
        return "off_peak"

    if 5 <= month <= 10:
        return get_tou_period_summer(hour)
    else:
        return get_tou_period_winter(hour)


def get_olu_period(hour, is_weekend):
    if is_weekend:
        return "off_peak"

    if 7 <= hour < 16 or 21 <= hour < 23:
        return "mid_peak"
    elif 16 <= hour < 21:
        return "on_peak"
    else:
        return "ulo"


# Function to determine the TOU period for a given hour in winter
def get_tou_period_winter(hour):
    if 7 <= hour < 11 or 17 <= hour < 19:
        return "mid_peak"
    elif 11 <= hour < 17:
        return "on_peak"
    else:
        return "off_peak"


# Function to determine the TOU period for a given hour in summer
def get_tou_period_summer(hour):
    if 7 <= hour < 11 or 17 <= hour < 19:
        return "on_peak"
    elif 11 <= hour < 17:
        return "mid_peak"
    else:
        return "off_peak"


# Assuming df_usage is your DataFrame with the usage data
# Ensure df_usage has 'start_time' as datetime objects and 'adjusted_value_kWh' as the energy consumption in kWh

# Define the TOU and Tiered rates in cents per kWh
tou_rates = {
    "on_peak": 18.2,
    "mid_peak": 12.2,
    "off_peak": 8.7,
}

# Define the TOU and Tiered rates in cents per kWh
ulo_rates = {
    "on_peak": 28.6,
    "mid_peak": 12.2,
    "off_peak": 8.7,
    "ulo": 2.8,  # Ultra-Low Overnight is not considered in this scenario
}

tiered_rates = {
    "tier_1": 10.3,  # Rate for up to the threshold
    "tier_2": 12.5,  # Rate for above the threshold
}

# Prepare dataframes for calculations
df_usage["month"] = df_usage["start_time"].dt.month
df_usage["tou_period"] = df_usage.apply(
    lambda row: get_tou_period(row["month"], row["hour_of_day"], row["is_weekend"]),
    axis=1,
)
df_usage["ulo_period"] = df_usage.apply(
    lambda row: get_olu_period(row["hour_of_day"], row["is_weekend"]), axis=1
)
df_usage["tou_rate_cents"] = df_usage["tou_period"].map(tou_rates)
df_usage["ulo_rate_cents"] = df_usage["ulo_period"].map(ulo_rates)

# Initialize the results dictionary
monthly_costs = {}


def sum_cost(month, prefix):
    # TOU Calculation
    monthly_data[f"{prefix}_cost"] = (
        monthly_data["adjusted_value_kWh"] * monthly_data[f"{prefix}_rate_cents"] / 100
    )
    return monthly_data[f"{prefix}_cost"].sum()


# Calculate the costs for each month
for month in df_usage["month"].unique():
    monthly_data = df_usage[df_usage["month"] == month]

    # Tiered Calculation
    tier_threshold = get_tier_threshold(month)
    tier_1_usage_total = min(monthly_data["adjusted_value_kWh"].sum(), tier_threshold)
    tier_2_usage_total = max(
        monthly_data["adjusted_value_kWh"].sum() - tier_threshold, 0
    )
    tiered_total_cost = (
        tier_1_usage_total * tiered_rates["tier_1"]
        + tier_2_usage_total * tiered_rates["tier_2"]
    ) / 100

    tou_total_cost = sum_cost(monthly_data, "tou")
    olu_total_cost = sum_cost(monthly_data, "ulo")

    min_cost = min(tou_total_cost, olu_total_cost, tiered_total_cost)
    max_cost = max(tou_total_cost, olu_total_cost, tiered_total_cost)

    if min_cost == tou_total_cost:
        best = "TOU"
    elif min_cost == olu_total_cost:
        best = "OLU"
    else:
        best = "Tiered"

    if max_cost == tou_total_cost:
        worst = "TOU"
    elif max_cost == olu_total_cost:
        worst = "OLU"
    else:
        worst = "Tiered"

    # Store results
    monthly_costs[month] = {
        "Total Usage": monthly_data["adjusted_value_kWh"].sum(),
        "TOU Cost": tou_total_cost,
        "OLU Cost": olu_total_cost,
        "Tiered Cost": tiered_total_cost,
        "Best": best,
        "Worst": worst,
    }

# Convert the results to a DataFrame for better visualization
df_monthly_costs = pd.DataFrame.from_dict(monthly_costs, orient="index")

# Display the monthly costs and differences
print(df_monthly_costs)
