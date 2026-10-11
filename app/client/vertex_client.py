from google import genai
import os

PROJECT_ID = os.getenv("PROJECT_ID")

client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location="global"
)