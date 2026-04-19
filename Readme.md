# Job-Delivery 🚀

A personalized job searching and resume matching tool powered by the Claude API.

## Features
- 🔍 Automated job searching on LinkedIn
- 📄 Resume matching powered by Claude AI
- 🪟 Windows support

## Prerequisites
- Windows OS
- Google Chrome installed
- An [Anthropic API key](https://console.anthropic.com/)

## Setup

### 1. Launch Chrome with remote debugging

Open CMD and run:

```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome-debug"
```

Then log in to your LinkedIn account in the opened browser.

### 2. Verify the debugger is running

Visit the following URL in a separate browser tab to confirm the connection:

```
http://localhost:9222/json/version
```

### 3. Set your API key

```cmd
set ANTHROPIC_API_KEY=your_api_key_here
```

### 4. Run the tool

```cmd
python jd_bg_cmp.py
```

## Notes
- The Chrome debugging session must remain open while using the tool
- Currently CHN version only 
