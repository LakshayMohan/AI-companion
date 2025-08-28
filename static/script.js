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
    const newSessionBtn = document.getElementById('newSessionBtn');
    const settingsBtn = document.getElementById('settingsBtn');
    const sidebar = document.getElementById('settingsSidebar');
    const sidebarOverlay = document.getElementById('sidebarOverlay');
    const closeSidebarBtn = document.getElementById('closeSidebarBtn');
    const saveSettingsBtn = document.getElementById('saveSettingsBtn');
    const inputMurf = document.getElementById('settingsMurfApiKey');
    const inputAssembly = document.getElementById('settingsAssemblyApiKey');
    const inputGemini = document.getElementById('settingsGeminiApiKey');
    const inputSpotifyId = document.getElementById('settingsSpotifyClientId');
    const inputSpotifySecret = document.getElementById('settingsSpotifyClientSecret');
    const inputOpenCage = document.getElementById('settingsOpenCageApiKey');

    // --- State Variables ---
    let mediaRecorder, audioChunks = [], sessionId = null, isRecording = false;
    
    // --- Audio Visualization Variables ---
    let audioContext, analyser, microphoneSource, audioEleSource, dataArray, animationFrameId;

    // --- Session Management ---
    window.onload = () => {
        const params = new URLSearchParams(window.location.search);
        sessionId = params.get('session_id') || `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        const newUrl = `${window.location.pathname}?session_id=${sessionId}`;
        window.history.replaceState({ path: newUrl }, '', newUrl);
        // Load any saved settings on page load
        loadSettingsIntoForm();
    };
    
    // --- Core Functionality ---
    recordBtn.addEventListener('click', () => {
        // Ensure AudioContext is created and resumed on the first user click
        if (!audioContext) {
            setupAudioContext();
        }
        audioContext.resume();

        if (isRecording) {
            stopRecording();
        } else {
            startRecording();
        }
    });

    // --- Top Actions: New Session and Settings ---
    if (newSessionBtn) {
        newSessionBtn.addEventListener('click', () => {
            const newId = `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
            const newUrl = `${window.location.pathname}?session_id=${newId}`;
            window.location.href = newUrl;
        });
    }

    function openSidebar() {
        sidebar.classList.add('open');
        sidebarOverlay.style.display = 'block';
        loadSettingsIntoForm();
    }
    function closeSidebar() {
        sidebar.classList.remove('open');
        sidebarOverlay.style.display = 'none';
    }

    if (settingsBtn) settingsBtn.addEventListener('click', openSidebar);
    if (closeSidebarBtn) closeSidebarBtn.addEventListener('click', closeSidebar);
    if (sidebarOverlay) sidebarOverlay.addEventListener('click', closeSidebar);

    if (saveSettingsBtn) {
        saveSettingsBtn.addEventListener('click', async () => {
            const settings = collectSettingsFromForm();
            saveSettingsToLocalStorage(settings);
            await syncSettingsToServer(settings);
            closeSidebar();
            statusDisplay.textContent = 'Settings saved for this session.';
        });
    }

    // --- Initialize Audio Context and Analyser ---
    function setupAudioContext() {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 128;
        dataArray = new Uint8Array(analyser.frequencyBinCount);
        // Connect the shared analyser to the final output
        analyser.connect(audioContext.destination);
    }

    async function startRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            resetUIState();

            // Connect Microphone to Analyser
            microphoneSource = audioContext.createMediaStreamSource(stream);
            microphoneSource.connect(analyser);
            startVisualizer();

            mediaRecorder = new MediaRecorder(stream);
            audioChunks = [];
            mediaRecorder.ondataavailable = event => audioChunks.push(event.data);
            mediaRecorder.onstop = () => {
                stream.getTracks().forEach(track => track.stop()); // Stop mic access
                processAudio();
            };
            mediaRecorder.start();
            isRecording = true;
            updateUIRecording(true);
        } catch (error) {
            statusDisplay.textContent = "Microphone access denied.";
            statusDisplay.classList.add('error');
        }
    }

    function stopRecording() {
        if (mediaRecorder && mediaRecorder.state !== "inactive") {
            mediaRecorder.stop();
            stopVisualizer();
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
        // Attach per-session overrides if present
        const overrides = loadSettingsFromLocalStorage();
        if (overrides) {
            for (const [key, value] of Object.entries(overrides)) {
                if (value) formData.append(key, value);
            }
        }
        try {
            const response = await fetch(`/agent/chat/${sessionId}`, { method: "POST", body: formData });
            const data = await response.json();
            updateTextOutputs(data.transcription, data.llm_response);
            if (!response.ok) throw data;
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
            if (error.audio_url) await playResponseAudio(error.audio_url, true);
        }
    }
    
    // MODIFIED: This function is now more robust for autoplay
    async function playResponseAudio(audioUrl, isErrorMessage = false) {
        try {
            // Ensure context is active before attempting to play
            if (audioContext.state === 'suspended') {
                await audioContext.resume();
            }

            // Disconnect the microphone source if it's still connected
            if (microphoneSource) {
                microphoneSource.disconnect();
                microphoneSource = null;
            }

            // Create a source for the <audio> element if it doesn't exist
            if (!audioEleSource) {
                audioEleSource = audioContext.createMediaElementSource(responseAudio);
                audioEleSource.connect(analyser);
            }
            
            responseAudio.src = audioUrl;
            await responseAudio.play();
            startVisualizer();
            
            responseAudio.onended = () => {
                stopVisualizer();
                statusDisplay.textContent = isErrorMessage ? "Error finished." : "Ready for your next question.";
            };
        } catch (e) {
            console.error("Failed to play response audio:", e);
            statusDisplay.textContent = "Could not play AI response. Click 'Record' to enable audio.";
            statusDisplay.classList.add('error');
        }
    }

    // --- UI and Visualizer Control Functions ---
    function startVisualizer() {
        audioVisualizer.style.display = 'block';
        const canvasCtx = audioVisualizer.getContext('2d');
        const centerX = audioVisualizer.width / 2;
        const centerY = audioVisualizer.height / 2;
        const radius = 75;

        function draw() {
            animationFrameId = requestAnimationFrame(draw);
            analyser.getByteFrequencyData(dataArray);
            canvasCtx.clearRect(0, 0, audioVisualizer.width, audioVisualizer.height);

            for (let i = 0; i < analyser.frequencyBinCount; i++) {
                const barHeight = dataArray[i] * 0.25;
                const angle = (i / analyser.frequencyBinCount) * 2 * Math.PI;
                const startX = centerX + radius * Math.cos(angle);
                const startY = centerY + radius * Math.sin(angle);
                const endX = centerX + (radius + barHeight) * Math.cos(angle);
                const endY = centerY + (radius + barHeight) * Math.sin(angle);
                const gradient = canvasCtx.createLinearGradient(startX, startY, endX, endY);
                gradient.addColorStop(0, '#9966ff');
                gradient.addColorStop(1, '#00ffcc');

                canvasCtx.strokeStyle = gradient;
                canvasCtx.lineWidth = 3;
                canvasCtx.beginPath();
                canvasCtx.moveTo(startX, startY);
                canvasCtx.lineTo(endX, endY);
                canvasCtx.stroke();
            }
        }
        draw();
    }

    function stopVisualizer() {
        if (animationFrameId) {
            cancelAnimationFrame(animationFrameId);
        }
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
    }

    function updateUIRecording(isRec) {
        recordBtn.classList.toggle('recording', isRec);
        recordBtnText.textContent = isRec ? "Stop" : "Record";
        statusDisplay.textContent = isRec ? "Listening..." : "Ready to start";
    }

    function updateTextOutputs(transcription, llmResponse) {
        if (transcription) {
            transcriptionOutput.textContent = transcription;
            transcriptionContainer.style.display = "block";
        }
        if (llmResponse) {
            llmResponseOutput.textContent = llmResponse;
            llmResponseContainer.style.display = "block";
        }
    }

    // --- Settings Helpers ---
    function storageKey() { return 'ai_companion_settings_v1'; }

    function collectSettingsFromForm() {
        return {
            MURF_API_KEY: inputMurf?.value?.trim() || '',
            ASSEMBLYAI_API_KEY: inputAssembly?.value?.trim() || '',
            GEMINI_API_KEY: inputGemini?.value?.trim() || '',
            SPOTIFY_CLIENT_ID: inputSpotifyId?.value?.trim() || '',
            SPOTIFY_CLIENT_SECRET: inputSpotifySecret?.value?.trim() || '',
            OPENCAGE_API_KEY: inputOpenCage?.value?.trim() || ''
        };
    }

    function saveSettingsToLocalStorage(settings) {
        try { localStorage.setItem(storageKey(), JSON.stringify(settings)); } catch {}
    }

    function loadSettingsFromLocalStorage() {
        try {
            const raw = localStorage.getItem(storageKey());
            return raw ? JSON.parse(raw) : null;
        } catch { return null; }
    }

    function loadSettingsIntoForm() {
        const saved = loadSettingsFromLocalStorage();
        if (!saved) return;
        if (inputMurf) inputMurf.value = saved.MURF_API_KEY || '';
        if (inputAssembly) inputAssembly.value = saved.ASSEMBLYAI_API_KEY || '';
        if (inputGemini) inputGemini.value = saved.GEMINI_API_KEY || '';
        if (inputSpotifyId) inputSpotifyId.value = saved.SPOTIFY_CLIENT_ID || '';
        if (inputSpotifySecret) inputSpotifySecret.value = saved.SPOTIFY_CLIENT_SECRET || '';
        if (inputOpenCage) inputOpenCage.value = saved.OPENCAGE_API_KEY || '';
    }

    async function syncSettingsToServer(settings) {
        try {
            const res = await fetch(`/config/${sessionId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });
            await res.json().catch(() => ({}));
        } catch (e) {
            console.warn('Failed to sync settings to server', e);
        }
    }
});
