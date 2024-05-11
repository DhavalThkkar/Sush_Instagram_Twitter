import streamlit as st
import pandas as pd
import re
from datetime import datetime
from instagrapi import Client, exceptions
from loguru import logger
import os
import time
import random

# Constants
SESSION_FILE = "session.json"
SESSION_DURATION = 86400  # 24 hours in seconds
LOG_FILE = "app.log"

# Configure Loguru
logger.add(LOG_FILE, rotation="10 MB")

# Define Helper Functions
def is_session_valid():
    """Check if the session file is valid and not expired."""
    if os.path.exists(SESSION_FILE):
        file_age = time.time() - os.path.getmtime(SESSION_FILE)
        if file_age < SESSION_DURATION:
            return True
    return False

def extract_ig_username(url):
    """Extracts Instagram username from a given URL."""
    if isinstance(url, str):
        match = re.search(r"instagram\.com/([^/?]+)", url)
        return match.group(1) if match else None
    return None

def extract_ig_usernames(text):
    """Extracts all Instagram usernames from a given text block."""
    return [extract_ig_username(url) for url in re.findall(r'https?://[^\s]+', text) if 'instagram.com' in url]

def count_posts_for_month(user_id, year, month, client):
    """Counts posts for a specific user within a specific month and returns post count and links."""
    posts = client.user_medias(user_id, amount=1000)
    start_date = datetime(year, month, 1)
    end_date = datetime(year, month + 1, 1) if month < 12 else datetime(year + 1, 1, 1)
    count = 0
    links = []
    for post in posts:
        post_date = post.taken_at.replace(tzinfo=None) if post.taken_at.tzinfo else post.taken_at
        if start_date <= post_date < end_date:
            count += 1
            links.append(f"https://www.instagram.com/p/{post.code}/")
    return count, links

def manual_search(username, start_year, start_month, end_year, end_month, client):
    """Manually search for a specific Instagram ID and display posts for a range of months."""
    all_posts = []
    try:
        user_id = client.user_id_from_username(username)
        for year in range(start_year, end_year + 1):
            for month in range(start_month if year == start_year else 1, end_month + 1 if year == end_year else 13):
                start_time = time.time()
                post_count, post_links = count_posts_for_month(user_id, year, month, client)
                month_name = datetime(year, month, 1).strftime('%B')
                all_posts.append({
                    "Instagram ID": username,
                    "Post Count": post_count,
                    "Year": year,
                    "Month": month_name,
                    "Links": " | ".join(post_links)
                })
                elapsed_time = time.time() - start_time
                completion_message = f"Completed {username} | {month_name} {year} | Posts: {post_count} | Time: {elapsed_time:.2f} sec"
                st.write(completion_message)
                logger.info(completion_message)
                time.sleep(random.uniform(1, 3))  # Add delay between requests
    except exceptions.UserNotFound:
        error_message = f"Error: User {username} not found."
        st.error(error_message)
        logger.error(error_message)
        all_posts.append({
            "Instagram ID": username,
            "Post Count": 0,
            "Year": "-",
            "Month": "-",
            "Links": "User not found"
        })
    except Exception as e:
        handle_exception(client, e)
        st.error(f"Error retrieving posts for {username}: {e}")
        all_posts.append({
            "Instagram ID": username,
            "Post Count": 0,
            "Year": "-",
            "Month": "-",
            "Links": "Error occurred"
        })
    return all_posts

def handle_exception(client, e):
    """Handle various exceptions from Instagram API."""
    if isinstance(e, BadPassword):
        logger.error(f"Bad Password: {e}")
        client.set_proxy(client.next_proxy().href)
        if client.relogin_attempt > 0:
            freeze("Manual login required", days=7)
            raise ReloginAttemptExceeded(e)
        client.settings = client.rebuild_client_settings()
        return client.update_client_settings(client.get_settings())
    elif isinstance(e, LoginRequired):
        logger.error(f"Login Required: {e}")
        client.relogin()
        return client.update_client_settings(client.get_settings())
    elif isinstance(e, ChallengeRequired):
        api_path = client.last_json.get("challenge", {}).get("api_path")
        if api_path == "/challenge/":
            client.set_proxy(client.next_proxy().href)
            client.settings = client.rebuild_client_settings()
        else:
            try:
                client.challenge_resolve(client.last_json)
            except ChallengeRequired as e:
                freeze("Manual Challenge Required", days=2)
                raise e
            except (ChallengeRequired, SelectContactPointRecoveryForm, RecaptchaChallengeForm) as e:
                freeze(str(e), days=4)
                raise e
            client.update_client_settings(client.get_settings())
        return True
    elif isinstance(e, FeedbackRequired):
        message = client.last_json["feedback_message"]
        logger.warning(f"Feedback Required: {message}")
        if "This action was blocked. Please try again later" in message:
            freeze(message, hours=12)
        elif "We restrict certain activity to protect our community" in message:
            freeze(message, hours=12)
        elif "Your account has been temporarily blocked" in message:
            freeze(message)
    elif isinstance(e, PleaseWaitFewMinutes):
        logger.warning(f"Please Wait: {e}")
        freeze(str(e), hours=1)
    raise e

def save_to_csv(data, filename):
    """Save results to a CSV file."""
    df = pd.DataFrame(data)
    df.to_csv(filename, index=False)

# Streamlit App
st.title("Instagram Data Processor")

# Sidebar for User Credentials
with st.sidebar:
    st.header("Instagram Credentials")
    
    # Instagram Credentials
    if not is_session_valid():
        USERNAME = st.text_input("Instagram Username")
        PASSWORD = st.text_input("Instagram Password", type="password")
    else:
        USERNAME, PASSWORD = None, None

# Initialize Instagram Client
client = Client()
client.delay_range = [1, 3]  # Add delays between requests
session_loaded = False

if is_session_valid():
    try:
        client.load_settings(SESSION_FILE)
        session_loaded = True
        logger.info("Session loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load session: {e}")
        session_loaded = False
else:
    if USERNAME and PASSWORD:
        logger.info("Attempting to login and save session.")
        try:
            client.login(USERNAME, PASSWORD)
            client.dump_settings(SESSION_FILE)
            logger.info("Logged in and saved session.")
            session_loaded = True
        except Exception as e:
            logger.error(f"Login failed: {e}")
            st.error("Login failed. Please check your credentials.")
            st.stop()

# Main App Section for Inputs
st.header("Input Details")

# User ID Input Section
st.subheader("Social Media Handles")
user_input = st.text_area("Enter Social Media Handles (one per line)")
lines = user_input.splitlines()
instagram_usernames = []

# Extract Instagram usernames
for line in lines:
    instagram_usernames.extend(extract_ig_usernames(line))

# Date Range Input
st.subheader("Specify Date Range")
col1, col2 = st.columns(2)
with col1:
    start_year = st.number_input("Start Year", min_value=2000, max_value=datetime.now().year, value=datetime.now().year)
    start_month = st.selectbox("Start Month", [datetime(2000, i, 1).strftime('%B') for i in range(1, 13)], key="start_month")
with col2:
    end_year = st.number_input("End Year", min_value=2000, max_value=datetime.now().year, value=datetime.now().year)
    end_month = st.selectbox("End Month", [datetime(2000, i, 1).strftime('%B') for i in range(1, 13)], key="end_month")

# Convert month names to numbers
start_month_num = datetime.strptime(start_month, '%B').month
end_month_num = datetime.strptime(end_month, '%B').month

# Initialize results container
results = {}

# Process All User IDs
if st.button("Process All IDs"):
    if not instagram_usernames:
        st.warning("No valid Instagram usernames found.")
    else:
        progress_bar = st.progress(0)
        total_ids = len(instagram_usernames)
        for idx, username in enumerate(instagram_usernames):
            posts_info = manual_search(username, start_year, start_month_num, end_year, end_month_num, client)
            results[username] = posts_info
            progress_bar.progress((idx + 1) / total_ids)
            st.write(f"Completed processing for {username}.")

# Display results
st.header("Results")
results_df = pd.DataFrame()
for username, posts_info in results.items():
    user_df = pd.DataFrame(posts_info)
    results_df = pd.concat([results_df, user_df], ignore_index=True)

# Generate dynamic filename
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
csv_filename = f"instagram_results_{timestamp}.csv"

# Save to CSV
save_to_csv(results_df.to_dict(orient="records"), csv_filename)

# Display the results in a table
st.dataframe(results_df)

# Download buttons
st.download_button("Download Results", open(csv_filename, "rb").read(), csv_filename, "text/csv")

# Download Log File
st.header("Download Logs")
if st.button("Download Log File"):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"processed_logs_{timestamp}.log"
    with open(LOG_FILE, "r") as log_file:
        lines = log_file.readlines()
    with open(log_filename, "w") as filtered_log_file:
        for line in lines:
            if "Completed" in line or "Error" in line:
                filtered_log_file.write(line)
    st.download_button("Download Log File", open(log_filename, "rb").read(), log_filename, "text/plain")

# Clean up old session file
if is_session_valid() and (time.time() - os.path.getmtime(SESSION_FILE)) >= SESSION_DURATION:
    os.remove(SESSION_FILE)
    logger.info("Old session file removed.")