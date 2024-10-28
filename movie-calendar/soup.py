import re
import os
import requests
import time
from urllib.parse import urlparse

# Regex to capture both font and image URLs
file_url_pattern = r"url\(['\"]?(https://web\.archive\.org/.+?\.(woff2?|ttf|eot|svg|gif|png|jpg))['\"]?\)"

# Load and parse the CSS file
with open("styles2.css", "r") as file:
    css_content = file.read()

# Find all unique file URLs (fonts and images)
matches = re.findall(file_url_pattern, css_content)
# Flatten list in case any non-string data was captured
matches = [match if isinstance(match, str) else match[0] for match in matches]

# Loop through each unique URL and download if not already present
for full_url in set(matches):
    # Ensure full_url is a string before parsing
    if isinstance(full_url, str):
        # Extract the filename from the URL
        filename = os.path.basename(urlparse(full_url).path)
        
        # Check if the file already exists to avoid re-downloading
        if not os.path.exists(filename):
            try:
                # Download the file
                response = requests.get(full_url, timeout=10)
                response.raise_for_status()

                # Save the file locally
                with open(filename, "wb") as file:
                    file.write(response.content)
                
                print(f"Downloaded: {filename}")
            except requests.RequestException as e:
                print(f"Failed to download {full_url}: {e}")
                time.sleep(5)  # Pause to avoid rapid retries in case of an error
        
        # Replace the full URL in CSS with just the filename for local reference
        css_content = css_content.replace(full_url, filename)

# Save the updated CSS content back to the file
with open("styles2.css", "w") as file:
    file.write(css_content)

print("CSS file updated, and all fonts and images downloaded.")
