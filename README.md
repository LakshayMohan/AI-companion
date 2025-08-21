# MurfAI - AI Voice Companion

A real-time AI voice companion application that uses WebSocket-based audio streaming for continuous recording and processing.

## Features

- **Real-time Audio Streaming**: Uses WebSocket connections to stream audio data from client to server in real-time
- **Audio Recording**: Records audio directly to WAV files on the server
- **Speech-to-Text**: Transcribes audio using AssemblyAI
- **AI Chat**: Generates responses using Google's Gemini AI
- **Text-to-Speech**: Converts AI responses to speech using Murf AI
- **Session Management**: Maintains conversation context per session

## WebSocket Audio Streaming

The application now supports real-time audio streaming using WebSockets:

### Client-Side
- Connects to WebSocket endpoint `/ws/audio/{session_id}`
- Streams audio data in real-time using `ScriptProcessorNode`
- Converts audio to 16-bit PCM format for efficient transmission
- Shows WebSocket connection status

### Server-Side
- WebSocket endpoint: `/ws/audio/{session_id}`
- Receives binary audio data from client
- Saves audio to WAV files in the `recordings/` directory
- Automatically handles connection lifecycle

## Setup

1. Install dependencies:
```bash
pip install fastapi uvicorn python-dotenv httpx assemblyai google-generativeai websockets
```

2. Create a `.env` file with your API keys:
```
MURF_API_KEY=your_murf_api_key
ASSEMBLYAI_API_KEY=your_assemblyai_api_key
GEMINI_API_KEY=your_gemini_api_key
```

3. Run the application:
```bash
uvicorn app:app --reload
```

## Usage

1. Open the application in your browser
2. Click "Record" to start recording audio
3. Speak into your microphone
4. Click "Stop" to end recording
5. The audio will be saved to the server as a WAV file
6. Use "Check Saved Recordings" to see all recorded files

## API Endpoints

- `GET /` - Main application page
- `GET /recordings` - List all saved audio recordings
- `POST /agent/chat/{session_id}` - Process audio and generate AI response
- `POST /tts` - Generate speech from text
- `GET /proxy-audio/` - Proxy audio files
- `WS /ws/audio/{session_id}` - WebSocket endpoint for audio streaming

## File Structure

```
murfAI/
├── app.py              # Main FastAPI application
├── static/
│   ├── index.html      # Main application page
│   ├── script.js       # Client-side JavaScript
│   └── styles.css      # Application styling
├── recordings/         # Saved audio recordings (auto-created)
└── README.md          # This file
```

## Technical Details

### Audio Format
- Sample Rate: 44.1 kHz
- Channels: Mono (1 channel)
- Bit Depth: 16-bit PCM
- Format: WAV

### WebSocket Protocol
- Binary data transmission
- Real-time audio streaming
- Automatic connection management
- Session-based recording

### Security Notes
- CORS is currently set to allow all origins (restrict in production)
- API keys should be kept secure
- WebSocket connections are session-specific

