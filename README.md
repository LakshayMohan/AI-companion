# MurfAI - AI Voice Companion

A real-time AI voice companion application that uses WebSocket-based audio streaming with AssemblyAI real-time transcription.

## Features

- **Real-time Audio Streaming**: Uses WebSocket connections to stream audio data from client to server in real-time
- **AssemblyAI Real-time Transcription**: Live speech-to-text transcription using AssemblyAI's streaming API
- **Audio Recording**: Records audio directly to WAV files on the server
- **AI Chat**: Generates responses using Google's Gemini AI
- **Text-to-Speech**: Converts AI responses to speech using Murf AI
- **Session Management**: Maintains conversation context per session

## AssemblyAI Real-time Transcription

The application now includes real-time speech-to-text transcription using AssemblyAI's streaming API:

### Features
- **Live Transcription**: Real-time transcription as you speak
- **Console Output**: Transcription results are printed to the server console
- **Proper Audio Format**: Automatically converts audio to 16kHz, 16-bit, mono PCM format
- **Session-based**: Each recording session has its own transcription stream

### Audio Format
- **Sample Rate**: 16kHz (required by AssemblyAI)
- **Bit Depth**: 16-bit PCM
- **Channels**: Mono (1 channel)
- **Format**: WAV files saved with proper headers

## WebSocket Audio Streaming

The application supports real-time audio streaming using WebSockets:

### Client-Side
- Connects to WebSocket endpoint `/ws/audio/{session_id}`
- Streams audio data in real-time using `ScriptProcessorNode`
- Converts audio to 16-bit PCM format for efficient transmission
- Shows WebSocket connection status and transcription status

### Server-Side
- WebSocket endpoint: `/ws/audio/{session_id}`
- Receives binary audio data from client
- Sends audio data to AssemblyAI for real-time transcription
- Saves audio to WAV files in the `recordings/` directory
- Automatically handles connection lifecycle

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
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
3. Speak into your microphone - you'll see real-time transcription in the server console
4. Click "Stop" to end recording
5. The audio will be saved to the server as a WAV file
6. Use "Check Saved Recordings" to see all recorded files

## Console Output

When recording, you'll see real-time transcription output in the server console:
```
🔗 AssemblyAI transcription connection opened
🎤 TRANSCRIPTION: Hello, how are you today?
🎤 TRANSCRIPTION: I'm doing well, thank you for asking
🔌 AssemblyAI transcription stopped
```

## API Endpoints

- `GET /` - Main application page
- `GET /recordings` - List all saved audio recordings
- `POST /agent/chat/{session_id}` - Process audio and generate AI response
- `POST /tts` - Generate speech from text
- `GET /proxy-audio/` - Proxy audio files
- `WS /ws/audio/{session_id}` - WebSocket endpoint for audio streaming with transcription

## File Structure

```
murfAI/
├── app.py              # Main FastAPI application with AssemblyAI integration
├── static/
│   ├── index.html      # Main application page
│   ├── script.js       # Client-side JavaScript with 16kHz audio processing
│   └── styles.css      # Application styling
├── recordings/         # Saved audio recordings (auto-created)
├── requirements.txt    # Python dependencies
└── README.md          # This file
```

## Technical Details

### Audio Format for AssemblyAI
- Sample Rate: 16kHz (required)
- Channels: Mono (1 channel)
- Bit Depth: 16-bit PCM
- Format: WAV

### WebSocket Protocol
- Binary data transmission
- Real-time audio streaming
- Automatic connection management
- Session-based recording and transcription

### AssemblyAI Integration
- Real-time streaming transcription
- Automatic audio format conversion
- Session-based transcriber instances
- Console output for transcription results

### Security Notes
- CORS is currently set to allow all origins (restrict in production)
- API keys should be kept secure
- WebSocket connections are session-specific

