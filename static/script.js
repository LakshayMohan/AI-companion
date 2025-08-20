// script.js
document.addEventListener('DOMContentLoaded', () => {
    // --- UI Elements ---
    const recordBtn = document.getElementById('recordBtn');
    const recordBtnText = document.getElementById('recordBtnText');
    const statusDisplay = document.getElementById("status");
    const responseAudio = document.getElementById("responseAudio");
    const audioVisualizer = document.getElementById('audioVisualizer'); // Canvas is still here but used as ring
    const transcriptionContainer = document.getElementById("transcription-container");
    const transcriptionOutput = document.getElementById("transcriptionOutput");
    const llmResponseContainer = document.getElementById("llm-response-container");
    const llmResponseOutput = document.getElementById("llmResponseOutput");

    // --- State & Audio Variables ---
    let mediaRecorder, audioChunks = [], sessionId = null, isRecording = false;
    let microphoneSource, animationId, sourceNode;
    
    // --- Audio Context & Analyser ---
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 256; // smoother pulse

    // --- Session Management ---
    window.onload = () => {
        const params = new URLSearchParams(window.location.search);
        sessionId = params.get('session_id') || `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        const newUrl = `${window.location.pathname}?session_id=${sessionId}`;
        window.history.replaceState({ path: newUrl }, '', newUrl);
    };

    // --- Record Button ---
    recordBtn.addEventListener('click', () => {
        audioContext.resume(); // Ensure context is active
        if (isRecording) {
            stopRecording();
        } else {
            startRecording();
        }
    });

    async function startRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            resetUIState();
            
            microphoneSource = audioContext.createMediaStreamSource(stream);
            microphoneSource.connect(analyser);
            
            startPulseEffect(); // <-- Use pulse effect instead of waveform
            
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
            stopPulseEffect();
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
            statusDisplay.textContent = "Recording too short.";
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
            
            updateTextOutputs(data.transcription, data.llm_response);
            
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
            
            updateTextOutputs(error.transcription, error.llm_response);
            
            if (error.audio_url) {
                await playResponseAudio(error.audio_url, true);
            }
        }
    }

    async function playResponseAudio(audioUrl, isErrorMessage = false) {
        try {
            if (audioContext.state === 'suspended') {
                await audioContext.resume();
            }
            stopPulseEffect();

            const proxiedUrl = `/proxy-audio/?url=${encodeURIComponent(audioUrl)}`;
            responseAudio.src = proxiedUrl;
            responseAudio.crossOrigin = "anonymous";
            responseAudio.style.display = 'block';

            sourceNode = audioContext.createMediaElementSource(responseAudio);
            sourceNode.connect(analyser);
            analyser.connect(audioContext.destination);

            startPulseEffect(); // pulse with AI audio

            await responseAudio.play();
            
            responseAudio.onended = () => {
                stopPulseEffect();
                if (sourceNode) {
                    sourceNode.disconnect();
                    sourceNode = null;
                }
                statusDisplay.textContent = isErrorMessage
                    ? "Error playback finished."
                    : "Ready for your next question.";
            };
        } catch (e) {
            console.error("Failed to play response audio:", e);
            statusDisplay.textContent = "Could not play AI response.";
            statusDisplay.classList.add('error');
            stopPulseEffect();
        }
    }

    // --- Pulse Effect (around record button) ---
    function startPulseEffect() {
        audioVisualizer.style.display = 'block';
        const canvasCtx = audioVisualizer.getContext('2d');
        const bufferLength = analyser.frequencyBinCount;
        const dataArray = new Uint8Array(bufferLength);

        function draw() {
            animationId = requestAnimationFrame(draw);
            analyser.getByteFrequencyData(dataArray);
            const avg = dataArray.reduce((a, b) => a + b, 0) / bufferLength;
            
            canvasCtx.clearRect(0, 0, audioVisualizer.width, audioVisualizer.height);

            const radius = 60 + avg / 4; // pulse size
            canvasCtx.beginPath();
            canvasCtx.arc(audioVisualizer.width / 2, audioVisualizer.height / 2, radius, 0, 2 * Math.PI);
            canvasCtx.strokeStyle = 'rgba(153, 102, 255, 0.7)';
            canvasCtx.lineWidth = 6;
            canvasCtx.stroke();
        }
        draw();
    }

    function stopPulseEffect() {
        if (animationId) {
            cancelAnimationFrame(animationId);
            animationId = null;
        }
        const canvasCtx = audioVisualizer.getContext('2d');
        canvasCtx.clearRect(0, 0, audioVisualizer.width, audioVisualizer.height);
        audioVisualizer.style.display = 'none';
    }

    // --- UI Functions ---
    function resetUIState() {
        transcriptionContainer.style.display = "none";
        llmResponseContainer.style.display = "none";
        transcriptionOutput.textContent = "";
        llmResponseOutput.textContent = "";
        statusDisplay.classList.remove('error');
    }
    
    function updateUIRecording(isRec) {
        recordBtn.classList.toggle('recording', isRec);
        recordBtnText.textContent = isRec ? "Stop" : "Record";
        statusDisplay.textContent = isRec ? "Listening..." : "Ready to start";
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
