import json
import requests
import os
from dotenv import load_dotenv

load_dotenv()


def default_function():
    return json.dumps({"error": "Function not implemented."})


def get_weather(location: str, unit: str):
    """Fetch the weather"""
    try:
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={location}&count=1&language=en&format=json"
        params = {}
        headers = {"Content-Type": "application/json"}
        #headers["Authorization"] = token
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        return json.dumps(data)
    except requests.exceptions.HTTPError as err:
        print(f"HTTP error occurred: {err}")
    except Exception as err:
        print(f"An error occurred: {err}")
    return json.dumps({"error": "An error occurred while fetching property info."})