// script.js
document.addEventListener('DOMContentLoaded', () => {
    // --- UI Elements ---
    const recordBtn = document.getElementById('recordBtn');
    const recordBtnText = document.getElementById('recordBtnText');
    const statusDisplay = document.getElementById("status");
    const responseAudio = document.getElementById("responseAudio");
    const audioVisualizer = document.getElementById('audioVisualizer');
    const transcriptionContainer = document.getElementById("transcription-container");
    const transcriptionOutput = document.getElementById("transcriptionOutput");
    const llmResponseContainer = document.getElementById("llm-response-container");
    const llmResponseOutput = document.getElementById("llmResponseOutput");
    const stopAudioBtn = document.getElementById('stopAudioBtn');
    const websocketStatus = document.getElementById('websocket-status');
    const transcriptionStatus = document.getElementById('transcription-status');


    // --- State & Audio Variables ---
    let mediaRecorder, audioChunks = [], sessionId = null, isRecording = false;
    let microphoneSource = null, animationId = null;
    let websocket = null;
    let processor = null;
    let stream = null;
    
    // --- Audio Contexts and Analyser Nodes ---
    const audioContext = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: 16000  // Set to 16kHz for AssemblyAI compatibility
    });
    const micAnalyser = audioContext.createAnalyser();
    const playbackAnalyser = audioContext.createAnalyser();
    micAnalyser.fftSize = 256;
    playbackAnalyser.fftSize = 256;
    const responseSourceNode = audioContext.createMediaElementSource(responseAudio);

    // --- Session Management ---
    window.onload = () => {
        const params = new URLSearchParams(window.location.search);
        sessionId = params.get('session_id') || `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        window.history.replaceState({ path: `${window.location.pathname}?session_id=${sessionId}` }, '', `${window.location.pathname}?session_id=${sessionId}`);
    };

    // --- WebSocket Audio Streaming ---
    async function connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/audio/${sessionId}`;
        
        try {
            websocket = new WebSocket(wsUrl);
            
            websocket.onopen = () => {
                console.log('WebSocket connected for audio streaming');
                websocketStatus.textContent = "WebSocket: Connected";
                websocketStatus.className = "websocket-status connected";
                transcriptionStatus.textContent = "Transcription: Active";
                transcriptionStatus.className = "transcription-status active";
                statusDisplay.textContent = "WebSocket connected. Ready to record.";
            };
            
            websocket.onmessage = (event) => {
                console.log('Server message:', event.data);
            };
            
            websocket.onerror = (error) => {
                console.error('WebSocket error:', error);
                websocketStatus.textContent = "WebSocket: Error";
                websocketStatus.className = "websocket-status error";
                statusDisplay.textContent = "WebSocket connection error.";
                statusDisplay.classList.add('error');
            };
            
            websocket.onclose = () => {
                console.log('WebSocket disconnected');
                websocketStatus.textContent = "WebSocket: Disconnected";
                websocketStatus.className = "websocket-status";
                transcriptionStatus.textContent = "Transcription: Inactive";
                transcriptionStatus.className = "transcription-status";
                websocket = null;
            };
            
        } catch (error) {
            console.error('Failed to connect WebSocket:', error);
            statusDisplay.textContent = "Failed to connect WebSocket.";
            statusDisplay.classList.add('error');
        }
    }

    function disconnectWebSocket() {
        if (websocket) {
            websocket.close();
            websocket = null;
        }
    }

    // --- Record Button ---
    recordBtn.addEventListener('click', () => {
        audioContext.resume();
        if (isRecording) stopRecording();
        else startRecording();
    });
    
    stopAudioBtn.addEventListener('click', () => {
        responseAudio.pause();
        responseAudio.currentTime = 0;
        stopPulseEffect();
        statusDisplay.textContent = "Playback stopped.";
        stopAudioBtn.style.display = 'none';
        disconnectPlayback();
    });



    function disconnectRecording() {
        if (microphoneSource) {
            microphoneSource.disconnect();
            microphoneSource = null;
        }
        if (processor) {
            processor.disconnect();
            processor = null;
        }
        if (stream) {
            stream.getTracks().forEach(track => track.stop());
            stream = null;
        }
        micAnalyser.disconnect();
    }

    function disconnectPlayback() {
        responseSourceNode.disconnect();
        playbackAnalyser.disconnect();
    }

    async function startRecording() {
        try {
            disconnectPlayback(); // Ensure playback path is torn down
            disconnectRecording(); // Ensure previous recording path is gone

            // Connect WebSocket first
            await connectWebSocket();
            
            // Get microphone stream with 16kHz sample rate for AssemblyAI
            stream = await navigator.mediaDevices.getUserMedia({ 
                audio: {
                    sampleRate: { ideal: 16000, exact: 16000 },  // Force 16kHz
                    channelCount: { ideal: 1, exact: 1 },        // Force mono
                    echoCancellation: true,
                    noiseSuppression: true
                } 
            });
            
            resetUIState();
            microphoneSource = audioContext.createMediaStreamSource(stream);
            microphoneSource.connect(micAnalyser);    // Do not connect to speakers!
            
            // Create script processor for real-time audio processing
            // Buffer size should be appropriate for 16kHz
            processor = audioContext.createScriptProcessor(2048, 1, 1);
            
            processor.onaudioprocess = (event) => {
                if (websocket && websocket.readyState === WebSocket.OPEN) {
                    const inputData = event.inputBuffer.getChannelData(0);
                    
                    // Convert float32 to int16 for smaller data size
                    // AssemblyAI expects 16-bit PCM data
                    const int16Data = new Int16Array(inputData.length);
                    for (let i = 0; i < inputData.length; i++) {
                        // Convert float32 (-1 to 1) to int16 (-32768 to 32767)
                        int16Data[i] = Math.max(-32768, Math.min(32767, inputData[i] * 32768));
                    }
                    
                    // Send audio data via WebSocket
                    websocket.send(int16Data.buffer);
                }
            };
            
            microphoneSource.connect(processor);
            processor.connect(audioContext.destination);
            
            startPulseEffect('recording');
            isRecording = true;
            updateUIRecording(true);
            
        } catch (error) {
            console.error("Microphone access error:", error);
            statusDisplay.textContent = "Microphone access denied.";
            statusDisplay.classList.add('error');
        }
    }

    function stopRecording() {
        if (isRecording) {
            isRecording = false;
            stopPulseEffect();
            disconnectRecording();
            disconnectWebSocket();
            updateUIRecording(false);
            statusDisplay.textContent = "Recording stopped. Audio saved to server.";
        }
    }

    // --- Legacy processAudio function (kept for compatibility) ---
    async function processAudio() {
        statusDisplay.textContent = "Processing audio...";
        disconnectRecording();
        const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
        if (audioBlob.size < 1000) {
            statusDisplay.textContent = "Recording too short. Please try again.";
            statusDisplay.classList.add('error');
            return;
        }
        const formData = new FormData();
        formData.append("file", audioBlob, "user_recording.webm");
        try {
            const response = await fetch(`/agent/chat/${sessionId}`, { method: "POST", body: formData });
            const data = await response.json();
            if (!response.ok) throw data;
            updateTextOutputs(data.transcription, data.llm_response);
            if (data.audio_url) {
                statusDisplay.textContent = "Response received!";
                await playResponseAudio(data.audio_url);
            } else {
                throw new Error("Response received, but audio link is missing.");
            }
        } catch (error) {
            const errorMessage = error.error || "Unexpected error.";
            statusDisplay.textContent = errorMessage;
            statusDisplay.classList.add('error');
            updateTextOutputs(error.transcription, error.llm_response);
            if (error.audio_url) {
                await playResponseAudio(error.audio_url, true);
            }
        }
    }

    async function playResponseAudio(audioUrl, isErrorMessage = false) {
        try {
            await audioContext.resume();
            responseSourceNode.connect(playbackAnalyser);
            playbackAnalyser.connect(audioContext.destination);

            responseAudio.src = `/proxy-audio/?url=${encodeURIComponent(audioUrl)}`;
            responseAudio.crossOrigin = "anonymous";
            startPulseEffect('playback');
            stopAudioBtn.style.display = 'block';
            await responseAudio.play();
            responseAudio.onended = () => {
                stopPulseEffect();
                stopAudioBtn.style.display = 'none';
                statusDisplay.textContent = isErrorMessage ? "Finished error playback." : "Ready for your next question.";
                disconnectPlayback();
            };
        } catch (e) {
            console.error("Failed to play response audio:", e);
            statusDisplay.textContent = "Could not play AI response.";
            statusDisplay.classList.add('error');
            stopPulseEffect();
            stopAudioBtn.style.display = 'none';
            disconnectPlayback();
        }
    }

    function startPulseEffect(mode = 'recording') {
        audioVisualizer.style.display = 'block';
        const canvasCtx = audioVisualizer.getContext('2d');
        const analyser = mode === 'recording' ? micAnalyser : playbackAnalyser;
        const bufferLength = analyser.frequencyBinCount;
        const dataArray = new Uint8Array(bufferLength);
        function draw() {
            animationId = requestAnimationFrame(draw);
            analyser.getByteFrequencyData(dataArray);
            const avg = dataArray.reduce((a, b) => a + b, 0) / bufferLength;
            canvasCtx.clearRect(0, 0, audioVisualizer.width, audioVisualizer.height);
            const radius = 60 + avg / 4;
            canvasCtx.beginPath();
            canvasCtx.arc(audioVisualizer.width / 2, audioVisualizer.height / 2, radius, 0, 2 * Math.PI);
            canvasCtx.strokeStyle = 'rgba(153, 102, 255, 0.7)';
            canvasCtx.lineWidth = 6;
            canvasCtx.stroke();
        }
        draw();
    }

    function stopPulseEffect() {
        if (animationId) cancelAnimationFrame(animationId);
        animationId = null;
        const canvasCtx = audioVisualizer.getContext('2d');
        canvasCtx.clearRect(0, 0, audioVisualizer.width, audioVisualizer.height);
        audioVisualizer.style.display = 'none';
    }

    function resetUIState() {
        transcriptionContainer.style.display = "none";
        llmResponseContainer.style.display = "none";
        transcriptionOutput.textContent = "";
        llmResponseOutput.textContent = "";
        statusDisplay.classList.remove('error');
        stopAudioBtn.style.display = 'none';
    }
    function updateUIRecording(isRec) {
        recordBtn.classList.toggle('recording', isRec);
        recordBtnText.textContent = isRec ? "Stop" : "Record";
        statusDisplay.textContent = isRec ? "Listening..." : "Ready to start";
    }
    function updateTextOutputs(transcription, llmResponse) {
        if (transcription) {
            transcriptionOutput.textContent = `"${transcription}"`;
            transcriptionContainer.style.display = "block";
        }
        if (llmResponse) {
            llmResponseOutput.textContent = `${llmResponse}`;
            llmResponseContainer.style.display = "block";
        }
    }
});
// --- End of script.js ---
// This script handles audio recording, playback, and UI updates for the application.