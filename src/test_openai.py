import sys
import os

print(f"Python version: {sys.version}")

try:
    import openai
    from openai import OpenAI
    print("SUCCESS: Successfully imported OpenAI from openai")
    print(f"OpenAI package file: {openai.__file__}")
    client = OpenAI(api_key="sk-test")
    print(f"SUCCESS: Successfully initialized OpenAI client: {client}")
except Exception as e:
    print(f"ERROR during OpenAI test: {e}")
    import traceback
    traceback.print_exc()
