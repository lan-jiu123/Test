import requests
import os

LONGCAT_API_KEY = "ak_2kM4125oJ1VY6dM3wO32z2IC1ZM7D"

def ask_ai(question: str):
    url = "https://api.longcat.chat/openai/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {LONGCAT_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "longcat-2.0",
        "messages": [
            {
                "role": "system",
                "content": "你是一个工业设备检修专家，请用专业、简洁方式回答问题。"
            },
            {
                "role": "user",
                "content": question
            }
        ],
        "temperature": 0.7
    }

    response = requests.post(url, json=data, headers=headers)
    result = response.json()

    return result["choices"][0]["message"]["content"]