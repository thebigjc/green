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

# Convert the start times to Eastern Time
eastern = timezone("US/Eastern")
df_usage["start_time"] = (
    df_usage["start_time"].dt.tz_localize("UTC").dt.tz_convert(eastern)
)

# Extract hour of day and day of week for TOU rate determination
df_usage["hour_of_day"] = df_usage["start_time"].dt.hour
df_usage["day_of_week"] = df_usage["start_time"].dt.weekday
df_usage["date"] = df_usage["start_time"].dt.date

# Define the TOU rates in cents per kWh
tou_rates = {
    "on_peak": 28.6,
    "mid_peak": 12.2,
    "off_peak": 8.7,
    "ulo": 2.8,  # Ultra-Low Overnight
}

# Define the TOU time periods for Weekday Winter (Nov 1 to Apr 30)
tou_periods_winter = {
    "on_peak": [(7, 11), (17, 19)],
    "mid_peak": [(11, 17)],
    "off_peak": [(19, 24), (0, 7)],
    "ulo": [(0, 7)],  # Assuming this applies for overnight
}

# Apply the powerOfTenMultiplier to adjust the usage values
df_usage["value_wh"] = df_usage["value"] * 10 ** int(
    reading_type_info["powerOfTenMultiplier"]
)
df_usage["value_kwh"] = df_usage["value_wh"] / 1000


# Function to determine the TOU period for a given hour and day
def get_tou_period(hour, day_of_week, date):
    # Check if the day is a weekend or a holiday
    is_weekend_or_holiday = (
        day_of_week >= 5
    )  # 5 and 6 correspond to Saturday and Sunday
    # For simplicity, we are not checking for statutory holidays here
    # but this can be included by checking the date against a list of holidays

    # During the weekend and holidays, all day is off-peak
    if is_weekend_or_holiday:
        return "off_peak"

    # Apply winter weekday TOU periods
    for period, hours in tou_periods_winter.items():
        for hour_range in hours:
            if hour_range[0] <= hour < hour_range[1]:
                return period
    return "off_peak"  # Default to off-peak if no match


# Assign the TOU period based on the hour of the day and day of the week
df_usage["tou_period"] = df_usage.apply(
    lambda row: get_tou_period(row["hour_of_day"], row["day_of_week"], row["date"]),
    axis=1,
)

# Map the TOU period to the rate in cents
df_usage["rate_cents"] = df_usage["tou_period"].map(tou_rates)

# Calculate the cost in dollars
df_usage["cost"] = (
    df_usage["value_kwh"] * df_usage["rate_cents"] / 100
)  # Convert cents to dollars

print(f"Total OLU Cost: ${df_usage['cost'].sum():.2f}")

# Define the tiered rates in cents per kWh
tiered_rates = {
    "tier_1": 10.3,  # Rate for up to the threshold
    "tier_2": 12.5,  # Rate for above the threshold
}

# Initialize the cost counters
monthly_tier_usage = {}
total_tiered_cost = 0.0


# Function to get the correct threshold based on the month
def get_tier_threshold(month):
    return (
        600 if 5 <= month <= 10 else 1000
    )  # Summer threshold is 600 kWh, winter threshold is 1000 kWh


# Sort the usage by start_time
df_usage_sorted = df_usage.sort_values(by="start_time")

# Calculate the tiered costs
for index, row in df_usage_sorted.iterrows():
    month = row["start_time"].month
    # Check if the month has changed or is not in the dictionary
    if month not in monthly_tier_usage:
        # Reset the tier usage for the new month
        monthly_tier_usage[month] = 0

    tier_threshold = get_tier_threshold(month)
    if monthly_tier_usage[month] < tier_threshold:
        # Calculate how much of the current usage can be billed at tier_1 rate
        tier_1_usage = min(row["value_kwh"], tier_threshold - monthly_tier_usage[month])
        # Calculate the cost for tier_1 usage and add it to the total cost
        total_tiered_cost += tier_1_usage * tiered_rates["tier_1"]
        # Update the tier usage for the month
        monthly_tier_usage[month] += tier_1_usage
        # Calculate if there's any usage that needs to be billed at tier_2 rate
        tier_2_usage = row["value_kwh"] - tier_1_usage
    else:
        # All usage is billed at tier_2 rate
        tier_2_usage = row["value_kwh"]

    # Calculate the cost for tier_2 usage and add it to the total cost
    total_tiered_cost += tier_2_usage * tiered_rates["tier_2"]

# Convert the cost from cent
total_tiered_cost /= 100

# Output the total cost for tiered pricing
print(f"Total Tiered Cost: ${total_tiered_cost:.2f}")
