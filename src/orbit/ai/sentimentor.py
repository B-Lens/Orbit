import requests
import time

# Replace with your keys
NEWSDATA_API_KEY = 'pub_79944755eb34ab46b70cdbb27e75686c147c6'

# Keywords to track
keywords = ['bitcoin', 'crypto', 'stock market', 'inflation']

def fetch_news():
    url = f'https://newsdata.io/api/1/news?apikey={NEWSDATA_API_KEY}&q={"%20OR%20".join(keywords)}&language=en'
    response = requests.get(url)
    if response.status_code != 200:
        print("Failed to fetch news:", response.status_code, response.text)
        return []  # or handle this error case gracefully
    print("Successfully fetched news data from NewsData API.")
    #Print the response for debugging
    print("Response:", response.text)
    try:
        news_data = response.json()
    except ValueError:
        print("Failed to parse News JSON:", response.text)
        return []  # or handle this error case gracefully

    if 'error' in news_data:
        print("Error in News API response. Check server logs or monitoring for details.")
        return []
    print("Successfully fetched news data.")
    articles = []
    for article in news_data.get('results', []):
        title = article.get('title') or ''
        description = article.get('description') or ''
        full_text = f"{title.strip()}. {description.strip()}"
        if full_text.strip():  # avoid empty entries
            articles.append(full_text)
    
    return articles
