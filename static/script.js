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

    // --- State & Audio Variables ---
    let mediaRecorder, sessionId = null, isRecording = false;
    let microphoneSource, animationId;

    // --- NEW: WebSocket and Streaming variables ---
    let socket;
    const RECORDING_INTERVAL_MS = 250; // Send audio chunks every 250ms

    // --- Audio Context & Analyser Initialization ---
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 2048;
    let audioSourceNode = null;

    // --- Session Management ---
    window.onload = () => {
        const params = new URLSearchParams(window.location.search);
        sessionId = params.get('session_id') || `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        const newUrl = `${window.location.pathname}?session_id=${sessionId}`;
        window.history.replaceState({ path: newUrl }, '', newUrl);
        statusDisplay.textContent = "Ready to start streaming.";
    };

    // --- Core Functionality ---
    recordBtn.addEventListener('click', () => {
        audioContext.resume();
        if (isRecording) {
            stopStreaming();
        } else {
            startStreaming();
        }
    });

    // --- NEW: WebSocket Streaming Logic ---
    async function startStreaming() {
        resetUIState();
        updateUIRecording(true, "Connecting...");

        // Determine WebSocket protocol (ws or wss)
        const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
        const wsUrl = `${wsProtocol}://${window.location.host}/ws/audio/${sessionId}`;

        socket = new WebSocket(wsUrl);

        socket.onopen = async () => {
            console.log("WebSocket connection established.");
            statusDisplay.textContent = "Connection open. Starting stream...";
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                
                microphoneSource = audioContext.createMediaStreamSource(stream);
                microphoneSource.connect(analyser);
                startVisualizer();

                mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
                
                // This event listener sends data to the server
                mediaRecorder.ondataavailable = (event) => {
                    if (event.data.size > 0 && socket.readyState === WebSocket.OPEN) {
                        socket.send(event.data);
                    }
                };
                
                mediaRecorder.onstop = () => {
                    console.log("MediaRecorder stopped.");
                    // Stop the microphone track
                    stream.getTracks().forEach(track => track.stop());
                    // Close the socket connection from the client-side
                    if (socket && socket.readyState === WebSocket.OPEN) {
                        socket.close();
                    }
                    stopVisualizer();
                    if (microphoneSource) {
                        microphoneSource.disconnect();
                        microphoneSource = null;
                    }
                };

                // Start recording and sending data at a regular interval
                mediaRecorder.start(RECORDING_INTERVAL_MS);
                isRecording = true;
                updateUIRecording(true); // Update button to "Stop" and status to "Listening..."

            } catch (error) {
                console.error("Microphone access error:", error);
                statusDisplay.textContent = "Microphone access denied.";
                statusDisplay.classList.add('error');
                updateUIRecording(false);
                if (socket) socket.close();
            }
        };

        socket.onclose = (event) => {
            console.log("WebSocket connection closed:", event.reason);
            statusDisplay.textContent = "Stream saved. Ready for next session.";
            isRecording = false;
            updateUIRecording(false);
            if (mediaRecorder && mediaRecorder.state === "recording") {
                mediaRecorder.stop();
            }
        };

        socket.onerror = (error) => {
            console.error("WebSocket error:", error);
            statusDisplay.textContent = "A connection error occurred.";
            statusDisplay.classList.add('error');
            isRecording = false;
            updateUIRecording(false);
        };
    }
    
    function stopStreaming() {
        if (mediaRecorder && mediaRecorder.state === "recording") {
            mediaRecorder.stop(); // This will trigger onstop, which handles cleanup
        }
    }


    /*
    --- PREVIOUS CODE: The section below is the old HTTP-based upload logic. ---
    --- It is commented out as requested to prioritize the new WebSocket streaming functionality. ---

    async function startRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            resetUIState();
            microphoneSource = audioContext.createMediaStreamSource(stream);
            microphoneSource.connect(analyser);
            startVisualizer();
            mediaRecorder = new MediaRecorder(stream);
            audioChunks = [];
            mediaRecorder.ondataavailable = event => audioChunks.push(event.data);
            mediaRecorder.onstop = () => {
                stream.getTracks().forEach(track => track.stop());
                processAudio();
            };
            mediaRecorder.start();
            isRecording = true;
            updateUIRecording(true);
        } catch (error) {
            console.error("Microphone access error:", error);
            statusDisplay.textContent = "Microphone access denied.";
            statusDisplay.classList.add('error');
        }
    }

    function stopRecording() {
        if (mediaRecorder && mediaRecorder.state !== "inactive") {
            mediaRecorder.stop();
            stopVisualizer();
            if (microphoneSource) {
                microphoneSource.disconnect();
                microphoneSource = null;
            }
        }
    }

    async function processAudio() {
        isRecording = false;
        updateUIRecording(false);
        statusDisplay.textContent = "Processing audio...";
        const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
        if (audioBlob.size < 1000) {
            statusDisplay.textContent = "Recording too short. Please speak longer.";
            statusDisplay.classList.add('error');
            return;
        }
        const formData = new FormData();
        formData.append("file", audioBlob, "user_recording.webm");
        try {
            const response = await fetch(`/agent/chat/${sessionId}`, { method: "POST", body: formData });
            const data = await response.json();
            
            if (!response.ok) {
                throw data; 
            }
            
            updateTextOutputs(data.transcription, data.llm_text);
            
            if (data.audio_url) {
                statusDisplay.textContent = "Response received!";
                await playResponseAudio(data.audio_url);
            } else {
                throw new Error("Response received, but audio link is missing.");
            }
        } catch (error) {
            const errorMessage = error.error || "An unexpected error occurred.";
            statusDisplay.textContent = errorMessage;
            statusDisplay.classList.add('error');
            updateTextOutputs(error.transcription, error.llm_text);
            if (error.audio_url) {
                await playResponseAudio(error.audio_url, true);
            }
        }
    }
    
    */

    // --- Audio Playback and Visualization (No changes needed here, kept for completeness) ---

    async function playResponseAudio(audioUrl, isErrorMessage = false) {
        try {
            if (audioContext.state === 'suspended') {
                await audioContext.resume();
            }
            stopVisualizer();
            const proxiedUrl = `/proxy-audio/?url=${encodeURIComponent(audioUrl)}`;
            responseAudio.src = proxiedUrl;
            responseAudio.crossOrigin = "anonymous";
            responseAudio.style.display = 'block';

            if (!audioSourceNode) {
                audioSourceNode = audioContext.createMediaElementSource(responseAudio);
                audioSourceNode.connect(analyser);
                analyser.connect(audioContext.destination);
            }
            startVisualizer();
            await responseAudio.play();
            
            responseAudio.onended = () => {
                stopVisualizer();
                statusDisplay.textContent = isErrorMessage
                    ? "Error playback finished."
                    : "Ready for your next question.";
            };
        } catch (e) {
            console.error("Failed to play response audio:", e);
            statusDisplay.textContent = "Could not play AI response.";
            statusDisplay.classList.add('error');
            stopVisualizer();
        }
    }

    function startVisualizer() {
        audioVisualizer.style.display = 'block';
        const canvasCtx = audioVisualizer.getContext('2d');
        const bufferLength = analyser.frequencyBinCount;
        const dataArray = new Uint8Array(bufferLength);
        function draw() {
            animationId = requestAnimationFrame(draw);
            analyser.getByteTimeDomainData(dataArray);
            canvasCtx.fillStyle = 'rgba(255, 255, 255, 0.05)';
            canvasCtx.fillRect(0, 0, audioVisualizer.width, audioVisualizer.height);
            canvasCtx.lineWidth = 2.5;
            canvasCtx.strokeStyle = '#9966ff';
            canvasCtx.beginPath();
            const sliceWidth = audioVisualizer.width * 1.0 / bufferLength;
            let x = 0;
            for (let i = 0; i < bufferLength; i++) {
                const v = dataArray[i] / 128.0;
                const y = v * audioVisualizer.height / 2;
                if (i === 0) {
                    canvasCtx.moveTo(x, y);
                } else {
                    canvasCtx.lineTo(x, y);
                }
                x += sliceWidth;
            }
            canvasCtx.lineTo(audioVisualizer.width, audioVisualizer.height / 2);
            canvasCtx.stroke();
        }
        draw();
    }

    function stopVisualizer() {
        if (animationId) {
            cancelAnimationFrame(animationId);
            animationId = null;
        }
        const canvasCtx = audioVisualizer.getContext('2d');
        canvasCtx.clearRect(0, 0, audioVisualizer.width, audioVisualizer.height);
        audioVisualizer.style.display = 'none';
    }

    // --- UI Control Functions ---
    function resetUIState() {
        transcriptionContainer.style.display = "none";
        llmResponseContainer.style.display = "none";
        transcriptionOutput.textContent = "";
        llmResponseOutput.textContent = "";
        statusDisplay.classList.remove('error');
    }

    function updateUIRecording(isRec, statusText = null) {
        recordBtn.classList.toggle('recording', isRec);
        recordBtnText.textContent = isRec ? "Stop" : "Record";
        if (statusText) {
            statusDisplay.textContent = statusText;
        } else {
            statusDisplay.textContent = isRec ? "Streaming..." : "Ready to start streaming.";
        }
    }

    function updateTextOutputs(transcription, llmResponse) {
        if (transcription) {
            transcriptionOutput.textContent = `You said: "${transcription}"`;
            transcriptionContainer.style.display = "block";
        }
        if (llmResponse) {
            llmResponseOutput.textContent = `AI: "${llmResponse}"`;
            llmResponseContainer.style.display = "block";
        }
    }
});
