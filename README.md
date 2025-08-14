# 30 Days of AI Voice Agents | Day 13: Documentation

# AI Voice Companion



This application allows a user to have a spoken conversation with an AI agent. It captures the user's voice, transcribes it to text, sends it to a large language model (LLM) for a response, converts the AI's text response back into audio, and plays it back to the user, creating a seamless conversational loop.

## ✨ Features

* **Real-Time Voice Interaction**: Speak directly to the AI and hear its response.
* **Dynamic Audio Visualization**: A circular, oscillating visualizer activates and revolves around the record button, reacting to the amplitude of both the user's voice (during recording) and the AI's voice (during playback).
* **Modern "Glassmorphism" UI**: A sleek, transparent interface that is both beautiful and intuitive.
* **Session-Based Context**: The conversation history is maintained within a session, allowing the AI to remember previous parts of the dialogue.
* **On-Screen Transcription**: Both the user's transcribed question and the AI's text response are displayed for clarity.



## 🏗️ Architecture

The application follows a client-server architecture. The entire conversational flow is managed through a single API endpoint, creating a robust and stateful interaction.

1. **Client (Frontend)**: The user clicks the record button. The browser's `MediaRecorder` API captures audio. A live visualizer provides feedback.
2. **API Request**: When recording stops, the audio data (as a Blob) is sent via a `POST` request to the backend server.
3. **Backend (FastAPI Server)**:
    * Receives the audio file.
    * **Speech-to-Text (STT)**: Uses an STT service (e.g., Deepgram) to transcribe the audio into text.
    * **Language Model (LLM)**: Sends the transcribed text to an LLM service (e.g., OpenAI's GPT) to generate a conversational response.
    * **Text-to-Speech (TTS)**: Takes the LLM's text response and uses a TTS service (e.g., ElevenLabs, Deepgram Aura) to convert it into high-quality audio.
4. **API Response**: The server returns a JSON object to the client containing the user's transcription, the AI's text response, and a URL for the generated AI audio.
5. **Client (Frontend)**:
    * Receives the JSON data and displays the transcriptions.
    * Automatically plays the AI's audio response.
    * The audio visualizer activates again, this time reacting to the AI's speech.

## 🛠️ Tech Stack

* **Frontend**:
    * HTML5
    * CSS3 (with animations and modern layout techniques)
    * Vanilla JavaScript (using `fetch`, `MediaRecorder`, and `Web Audio API`)
* **Backend**:
    * Python 3.10+
    * FastAPI (for building the high-performance API)
    * Uvicorn (as the ASGI server)
* **AI Services**:
    * **Speech-to-Text**: Deepgram
    * **LLM**: OpenAI
    * **Text-to-Speech**: ElevenLabs or Deepgram


## 🚀 Getting Started

Follow these instructions to get a copy of the project up and running on your local machine.

### Prerequisites

* Python 3.10 or later
* `pip` package manager
* An account and API key for the following services:
    * [Deepgram](https://deepgram.com/) (for STT)
    * [OpenAI](https://openai.com/) (for LLM)
    * [ElevenLabs](https://elevenlabs.io/) (for TTS) - *or you can use Deepgram's TTS*


### Backend Setup

1. **Clone the repository:**

```sh
git clone https://github.com/your-username/ai-voice-companion.git
cd ai-voice-companion
```

2. **Create and activate a virtual environment:**
    * **macOS/Linux:**

```sh
python3 -m venv venv
source venv/bin/activate
```

    * **Windows:**

```sh
python -m venv venv
.\venv\Scripts\activate
```

3. **Install the required Python packages:**
*(You will need a `requirements.txt` file containing the dependencies)*

```sh
pip install -r requirements.txt
```

Your `requirements.txt` should look something like this:

```
fastapi
uvicorn[standard]
python-dotenv
deepgram-sdk
openai
elevenlabs
```

4. **Set up environment variables:**
Create a file named `.env` in the root of the project directory and add your API keys:

```
DEEPGRAM_API_KEY="YOUR_DEEPGRAM_API_KEY_HERE"
OPENAI_API_KEY="YOUR_OPENAI_API_KEY_HERE"
ELEVENLABS_API_KEY="YOUR_ELEVENLABS_API_KEY_HERE"
```

*Note: Your Python code must be configured to load these variables (e.g., using the `python-dotenv` library).*
5. **Run the backend server:**

```sh
uvicorn main:app --reload
```

The server will start, typically on `http://127.0.0.1:8000`. The `--reload` flag automatically restarts the server when you make code changes.

### Frontend

The frontend is served directly by the FastAPI backend. No separate build step is required.

## 💻 Usage

1. Once the backend server is running, open your web browser and navigate to `http://127.0.0.1:8000`.
2. The AI Voice Companion interface will load.
3. Click the central "Record" button to start recording. Your browser will ask for microphone permission the first time.
4. The visualizer will activate, showing that it's listening. Speak your question or command.
5. Click the "Stop" button when you are finished.
6. The application will process your audio. The AI's response will play back automatically, and the visualizer will activate again.
7. The conversation text will appear on screen.

## 📁 Project Structure

```
.
├── main.py             # FastAPI application logic
├── requirements.txt    # Python dependencies
├── .env                # Environment variables (ignored by git)
├── README.md           # This file
└── static/
    ├── index.html      # The main HTML file for the UI
    ├── script.js       # All frontend JavaScript logic
    └── styles.css      # All CSS for styling
```


## 📄 License

This project is licensed under the MIT License - see the `LICENSE` file for details.

