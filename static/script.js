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
    let workletNode = null;
    let base64AudioChunks = [];
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
                websocketStatus.textContent = "WebSocket: Connected";
                websocketStatus.className = "websocket-status connected";
                transcriptionStatus.textContent = "Transcription: Active";
                transcriptionStatus.className = "transcription-status active";
                statusDisplay.textContent = "WebSocket connected. Ready to record.";
            };
            
            websocket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    switch (data.type) {
                        case 'transcription':
                            handleTranscription(data);
                            break;
                        case 'llm_start':
                            handleLLMStart(data);
                            break;
                        case 'llm_chunk':
                            handleLLMChunk(data);
                            break;
                        case 'llm_complete':
                            handleLLMComplete(data);
                            break;
                        case 'llm_error':
                            handleLLMError(data);
                            break;
                        case 'murf_audio_chunk': {
                            if (data.audio) {
                                base64AudioChunks.push(data.audio);
                            }
                            break;
                        }
                        case 'murf_audio_final': {
                            // Seamless assembled playback: play all saved chunks as one WAV
                            try { murfFinalReceived = true; playSavedChunks(); } catch (_) {}
                            break;
                        }
                        default:
                            break;
                    }
                } catch (error) {
                    // ignore non-JSON messages
                }
            };
            
            websocket.onerror = (error) => {
                console.error('WebSocket error:', error);
                websocketStatus.textContent = "WebSocket: Error";
                websocketStatus.className = "websocket-status error";
                statusDisplay.textContent = "WebSocket connection error.";
                statusDisplay.classList.add('error');
            };
            
            websocket.onclose = () => {
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
        if (processor) { try { processor.disconnect(); } catch(_){} processor = null; }
        if (workletNode) { try { workletNode.disconnect(); } catch(_){} workletNode = null; }
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
            
            // Prefer AudioWorkletNode (ScriptProcessorNode is deprecated)
            try {
                await audioContext.audioWorklet.addModule('/static/recorderWorklet.js');
                workletNode = new AudioWorkletNode(audioContext, 'recorder-worklet');
                workletNode.port.onmessage = (e) => {
                    if (websocket && websocket.readyState === WebSocket.OPEN) {
                        // Forward PCM16 buffer directly from the worklet
                        if (e.data && e.data.byteLength) {
                            websocket.send(e.data);
                        }
                    }
                };
                microphoneSource.connect(workletNode);
            } catch (e) {
                console.error('AudioWorklet init failed');
            }
            
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
                statusDisplay.textContent = "Streaming stopped.";
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
            responseAudio.controls = true;
            responseAudio.style.display = 'block';
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
    
    function handleTranscription(data) {
        const { transcript, end_of_turn, turn_is_formatted, turn_order } = data;
        
        if (transcript) {
            
            // Always show the transcription container
            transcriptionContainer.style.display = "block";
            
            // Create a more appealing transcription display
            let displayText = transcript;
            
            // Add turn number if it's a new turn
            if (turn_order && turn_order > 0) {
                displayText = `<span class="turn-number">Turn ${turn_order}</span> ${transcript}`;
            }
            
            // Add live indicator for ongoing transcription
            if (!end_of_turn) {
                displayText += ' <span class="live-indicator">●</span>';
            }
            
            // Update the transcription display
            transcriptionOutput.innerHTML = displayText;
            
            // Apply styling based on transcription state
            if (turn_is_formatted) {
                transcriptionOutput.classList.add('formatted');
                transcriptionOutput.classList.remove('live');
                
            } else {
                transcriptionOutput.classList.add('live');
                transcriptionOutput.classList.remove('formatted');
            }
            
            // Show end of turn indicator
            if (end_of_turn) {
                transcriptionOutput.innerHTML += ' <span class="end-turn">✓ Complete</span>';
                statusDisplay.textContent = "Turn completed. Ready for next input.";
                transcriptionOutput.classList.remove('live');
            } else {
                statusDisplay.textContent = "Listening...";
            }
            
            // Scroll to show the latest transcription
            transcriptionContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } else {
            // no-op
        }
    }

    // --- LLM Streaming Functions ---
    let currentLLMResponse = "";
    let murfFinalReceived = false;
    
    function handleLLMStart(data) {
        currentLLMResponse = "";
        murfFinalReceived = false;
        base64AudioChunks = [];
        
        // Show the LLM response container
        llmResponseContainer.style.display = "block";
        llmResponseOutput.textContent = "";
        
        // Update status
        statusDisplay.textContent = "AI is thinking...";
        statusDisplay.classList.remove('error');
        
        // Scroll to show the LLM response area
        llmResponseContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    
    function handleLLMChunk(data) {
        // Ensure container visible even if start event was missed
        llmResponseContainer.style.display = "block";
        if (data.text) {
            currentLLMResponse += data.text;
            llmResponseOutput.textContent = currentLLMResponse;
            
            // Add typing indicator
            llmResponseOutput.innerHTML = currentLLMResponse + '<span class="typing-indicator">|</span>';
            
            // Scroll to keep the latest text visible
            llmResponseOutput.scrollTop = llmResponseOutput.scrollHeight;
        }
    }
    
    function handleLLMComplete(data) {
        // Ensure container visible even if start event was missed
        llmResponseContainer.style.display = "block";
        
        // Remove typing indicator and show final response
        llmResponseOutput.textContent = data.full_response || currentLLMResponse;
        
        // Update status
        statusDisplay.textContent = "AI response completed. Ready for next input.";
        
        // Add completion styling
        llmResponseOutput.classList.add('completed');
        // Fallback: if Murf didn't signal final but we have chunks, play them
        if (!murfFinalReceived && base64AudioChunks && base64AudioChunks.length > 0) {
            try { setTimeout(() => playSavedChunks(), 200); } catch(_) {}
        }
    }

    // ------------------ WAV Assembly for Murf Chunks -----------------
    function base64ToUint8Array(base64) {
        const binary = atob(base64);
        const len = binary.length;
        const bytes = new Uint8Array(len);
        for (let i = 0; i < len; i++) bytes[i] = binary.charCodeAt(i);
        return bytes;
    }

    function createWavHeader(dataLength, sampleRate = 44100, numChannels = 1, bitDepth = 16) {
        const blockAlign = (numChannels * bitDepth) / 8;
        const byteRate = sampleRate * blockAlign;
        const buffer = new ArrayBuffer(44);
        const view = new DataView(buffer);
        function writeStr(offset, str) {
            for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
        }
        writeStr(0, "RIFF");
        view.setUint32(4, 36 + dataLength, true);
        writeStr(8, "WAVE");
        writeStr(12, "fmt ");
        view.setUint32(16, 16, true);
        view.setUint16(20, 1, true);
        view.setUint16(22, numChannels, true);
        view.setUint32(24, sampleRate, true);
        view.setUint32(28, byteRate, true);
        view.setUint16(32, blockAlign, true);
        view.setUint16(34, bitDepth, true);
        writeStr(36, "data");
        view.setUint32(40, dataLength, true);
        return new Uint8Array(buffer);
    }

    function playCombinedWavChunks(base64Chunks) {
        if (!base64Chunks || base64Chunks.length === 0) return;
        const pcmParts = [];
        for (let i = 0; i < base64Chunks.length; i++) {
            const bytes = base64ToUint8Array(base64Chunks[i]);
            if (i === 0) {
                pcmParts.push(bytes.slice(44));
            } else {
                pcmParts.push(bytes);
            }
        }
        const totalPcmLength = pcmParts.reduce((sum, arr) => sum + arr.length, 0);
        const totalPcm = new Uint8Array(totalPcmLength);
        let offset = 0;
        for (const part of pcmParts) {
            totalPcm.set(part, offset);
            offset += part.length;
        }
        const wavHeader = createWavHeader(totalPcm.length, 44100, 1, 16);
        const finalWav = new Uint8Array(wavHeader.length + totalPcm.length);
        finalWav.set(wavHeader, 0);
        finalWav.set(totalPcm, wavHeader.length);
        const blob = new Blob([finalWav], { type: "audio/wav" });
        const url = URL.createObjectURL(blob);

        try {
            disconnectPlayback();
            responseSourceNode.connect(playbackAnalyser);
            playbackAnalyser.connect(audioContext.destination);
        } catch (_) {}

        responseAudio.src = url;
        responseAudio.crossOrigin = "anonymous";
        responseAudio.controls = true;
        responseAudio.style.display = 'block';
        startPulseEffect('playback');
        stopAudioBtn.style.display = 'block';
        audioContext.resume().catch(() => {});
        responseAudio.play().catch(() => {});
        responseAudio.onended = () => {
            stopPulseEffect();
            stopAudioBtn.style.display = 'none';
            statusDisplay.textContent = "Ready for your next question.";
            disconnectPlayback();
        };
    }

    function playSavedChunks() {
        const saved = base64AudioChunks;
        if (saved && saved.length > 0) {
            playCombinedWavChunks(saved);
        }
    }
    // ------------------ END WAV Assembly -----------------
    
    function handleLLMError(data) {
        console.error('LLM error:', data.error);
        
        // Show error in LLM response area
        llmResponseContainer.style.display = "block";
        llmResponseOutput.textContent = `Error: ${data.error}`;
        llmResponseOutput.classList.add('error');
        
        // Update status
        statusDisplay.textContent = "AI encountered an error.";
        statusDisplay.classList.add('error');
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