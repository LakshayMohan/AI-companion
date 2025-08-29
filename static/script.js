// script.js
console.log('Script loaded successfully');

// Fallback: if DOM is already loaded, run setup immediately
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        console.log('DOMContentLoaded event fired');
        setupConfigButton();
    });
} else {
    // DOM already loaded
    console.log('DOM already loaded, running setup immediately');
    setupConfigButton();
}

function setupConfigButton() {
    console.log('Setting up config button...');

    // --- Config Modal ---
    const configBtn = document.getElementById('configBtn');
    const configModal = document.getElementById('configModal');
    const closeConfigModal = document.getElementById('closeConfigModal');
    const saveConfig = document.getElementById('saveConfig');
    const cancelConfig = document.getElementById('cancelConfig');

    // Input elements
    const murfKey = document.getElementById('murfKey');
    const assemblyKey = document.getElementById('assemblyKey');
    const geminiKey = document.getElementById('geminiKey');
    const opencageKey = document.getElementById('opencageKey');
    const spotifyClientId = document.getElementById('spotifyClientId');
    const spotifyClientSecret = document.getElementById('spotifyClientSecret');

    // Load saved API keys on page load
    function loadApiKeys() {
        if (murfKey) murfKey.value = localStorage.getItem('MURF_API_KEY') || '';
        if (assemblyKey) assemblyKey.value = localStorage.getItem('ASSEMBLYAI_API_KEY') || '';
        if (geminiKey) geminiKey.value = localStorage.getItem('GEMINI_API_KEY') || '';
        if (opencageKey) opencageKey.value = localStorage.getItem('OPENCAGE_API_KEY') || '';
        if (spotifyClientId) spotifyClientId.value = localStorage.getItem('SPOTIFY_CLIENT_ID') || '';
        if (spotifyClientSecret) spotifyClientSecret.value = localStorage.getItem('SPOTIFY_CLIENT_SECRET') || '';
    }

    // Save API keys to localStorage
    function saveApiKeys() {
        if (murfKey) localStorage.setItem('MURF_API_KEY', murfKey.value);
        if (assemblyKey) localStorage.setItem('ASSEMBLYAI_API_KEY', assemblyKey.value);
        if (geminiKey) localStorage.setItem('GEMINI_API_KEY', geminiKey.value);
        if (opencageKey) localStorage.setItem('OPENCAGE_API_KEY', opencageKey.value);
        if (spotifyClientId) localStorage.setItem('SPOTIFY_CLIENT_ID', spotifyClientId.value);
        if (spotifyClientSecret) localStorage.setItem('SPOTIFY_CLIENT_SECRET', spotifyClientSecret.value);
    }

    // Config button click handler
    if (configBtn) {
        console.log('Config button found, adding click handler');
        configBtn.addEventListener('click', function() {
            console.log('Config button clicked');
            loadApiKeys();
            if (configModal) {
                configModal.style.display = 'flex';
            }
        });
    } else {
        console.error('Config button not found!');
    }

    // Close config modal
    if (closeConfigModal) {
        closeConfigModal.addEventListener('click', () => {
            if (configModal) configModal.style.display = 'none';
        });
    }

    // Cancel config
    if (cancelConfig) {
        cancelConfig.addEventListener('click', () => {
            if (configModal) configModal.style.display = 'none';
        });
    }

    // Save config
    if (saveConfig) {
        saveConfig.addEventListener('click', () => {
            saveApiKeys();
            if (configModal) configModal.style.display = 'none';
            // Optional: Show success message
            const statusDisplay = document.getElementById('status');
            if (statusDisplay) {
                statusDisplay.textContent = 'API keys saved successfully!';
                setTimeout(() => {
                    statusDisplay.textContent = 'Ready to start';
                }, 2000);
            }
        });
    }

    // Close modal when clicking outside
    if (configModal) {
        configModal.addEventListener('click', (e) => {
            if (e.target === configModal) {
                configModal.style.display = 'none';
            }
        });
    }

    // Load API keys on page load
    loadApiKeys();
}

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
                            // If server provided a fully assembled base64 WAV, play it directly
                            murfFinalReceived = true;
                            if (data.audio_b64) {
                                try {
                                    const bytes = base64ToUint8Array(data.audio_b64);
                                    const blob = new Blob([bytes], { type: 'audio/wav' });
                                    const url = URL.createObjectURL(blob);
                                    // Clear any saved chunks to free memory
                                    base64AudioChunks = [];
                                    // mark playback active and cancel fallback
                                    responsePlaying = true;
                                    if (llmFallbackTimer) { clearTimeout(llmFallbackTimer); llmFallbackTimer = null; }
                                    disconnectPlayback();
                                    responseSourceNode.connect(playbackAnalyser);
                                    playbackAnalyser.connect(audioContext.destination);
                                    responseAudio.src = url;
                                    responseAudio.crossOrigin = 'anonymous';
                                    responseAudio.controls = true;
                                    responseAudio.style.display = 'block';
                                    startPulseEffect('playback');
                                    stopAudioBtn.style.display = 'block';
                                    audioContext.resume().catch(() => {});
                                    responseAudio.play().catch(() => {});
                                    responseAudio.onended = () => {
                                        stopPulseEffect();
                                        stopAudioBtn.style.display = 'none';
                                        statusDisplay.textContent = 'Ready for your next question.';
                                        responsePlaying = false;
                                        disconnectPlayback();
                                    };
                                } catch (e) {
                                    // fallback to playing accumulated chunks
                                    try { playSavedChunks(); } catch (_) {}
                                }
                            } else {
                                try { playSavedChunks(); } catch (_) {}
                            }
                            break;
                        }
                        case 'playlist_recommendations': {
                            try {
                                const pls = data.playlists || [];
                                const mood = data.mood || '';
                                appendPlaylistCards(pls, mood);
                            } catch (e) {}
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

    // ---------------- Chat UI helpers ----------------
    function appendUserBubble(text) {
        if (!chatContainer) return;
    const row = document.createElement('div');
    row.className = 'chat-row user';
    const avatar = document.createElement('div');
    avatar.className = 'avatar user';
    avatar.innerHTML = '<img src="/static/avatars/happy-face.png" alt="user"/>';
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble user';
    bubble.textContent = text;
    row.appendChild(avatar);
    row.appendChild(bubble);
    chatContainer.appendChild(row);
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    let currentAIBubble = null;
    function appendAIBubbleStart() {
        if (!chatContainer) return;
    const row = document.createElement('div');
    row.className = 'chat-row ai';
    const avatar = document.createElement('div');
    avatar.className = 'avatar ai';
    avatar.innerHTML = '<img src="/static/avatars/smile.png" alt="ai"/>';
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble ai';
    bubble.textContent = '';
    row.appendChild(bubble);
    row.appendChild(avatar);
    chatContainer.appendChild(row);
    chatContainer.scrollTop = chatContainer.scrollHeight;
    currentAIBubble = bubble;
    }

    function appendAIBubbleChunk(text) {
        if (!currentAIBubble) appendAIBubbleStart();
        // append text progressively
        currentAIBubble.textContent = (currentAIBubble.textContent || '') + text;
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    function finalizeAIBubble(text) {
        if (!currentAIBubble) {
            appendAIBubbleStart();
        }
        if (text) currentAIBubble.textContent = text;
        currentAIBubble = null;
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    function appendPlaylistCards(playlists, mood) {
        if (!chatContainer) return;
        if (!playlists || playlists.length === 0) return;

        const row = document.createElement('div');
        row.className = 'chat-row ai';

        const container = document.createElement('div');
        container.className = 'playlist-cards';
        const header = document.createElement('div');
        header.className = 'playlist-header';
        header.textContent = mood ? `Playlists for mood: ${mood}` : 'Playlist recommendations';
        container.appendChild(header);

        const list = document.createElement('div');
        list.className = 'playlist-list';
        playlists.forEach(p => {
            const card = document.createElement('a');
            card.className = 'playlist-card';
            card.href = p.url || '#';
            if (p.id) card.dataset.playlistId = p.id;
            card.target = '_blank';
            card.rel = 'noopener noreferrer';

            const img = document.createElement('img');
            img.className = 'playlist-image';
            img.src = p.image || '/static/avatars/smile.png';
            img.alt = p.name || 'playlist';
            card.appendChild(img);

            const meta = document.createElement('div');
            meta.className = 'playlist-meta';
            const title = document.createElement('div');
            title.className = 'playlist-title';
            title.textContent = p.name || 'Untitled';
            meta.appendChild(title);
            card.appendChild(meta);

            list.appendChild(card);
        });

        container.appendChild(list);
        // append a placeholder avatar at right for ai style
        const avatar = document.createElement('div');
        avatar.className = 'avatar ai';
        avatar.innerHTML = '<img src="/static/avatars/smile.png" alt="ai"/>';

        row.appendChild(container);
        row.appendChild(avatar);
        chatContainer.appendChild(row);
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    // --- Playlist modal & music bar logic ---
    const playlistModal = document.getElementById('playlistModal');
    const closePlaylistModal = document.getElementById('closePlaylistModal');
    const playlistModalTitle = document.getElementById('playlistModalTitle');
    const playlistTracksNode = document.getElementById('playlistTracks');
    const playerPlayBtn = document.getElementById('playerPlay');
    const playerPrevBtn = document.getElementById('playerPrev');
    const playerNextBtn = document.getElementById('playerNext');
    const playerProgressBar = document.querySelector('.player-progress-bar');

    const musicBar = document.getElementById('musicBar');
    const musicBarImage = document.getElementById('musicBarImage');
    const musicBarTitle = document.getElementById('musicBarTitle');
    const musicBarArtist = document.getElementById('musicBarArtist');
    const musicBarPlay = document.getElementById('musicBarPlay');
    const musicBarProgressFill = document.querySelector('.music-bar-progress-fill');

    let currentPlaylist = [];
    let currentTrackIndex = 0;
    let audioPlayer = new Audio();
    let progressTimer = null;

    function openPlaylistModal(playlist) {
        // playlist: {name, url, id, image}
        if (!playlist) return;
        playlistModalTitle.textContent = playlist.name || 'Playlist';
        playlistTracksNode.innerHTML = '<div class="loading">Loading tracks…</div>';
        playlistModal.style.display = 'flex';

        // fetch tracks
        fetch(`/spotify/playlist/${encodeURIComponent(playlist.id)}`)
            .then(r => r.json())
            .then(data => {
                renderPlaylistTracks(data.tracks || []);
                currentPlaylist = data.tracks || [];
                currentTrackIndex = 0;
            }).catch(err => {
                playlistTracksNode.innerHTML = '<div class="error">Could not load tracks.</div>';
            });
    }

    function closeModal() {
        playlistModal.style.display = 'none';
    }
    if (closePlaylistModal) closePlaylistModal.addEventListener('click', closeModal);

    function renderPlaylistTracks(tracks) {
        playlistTracksNode.innerHTML = '';
        tracks.forEach((t, i) => {
            const tr = document.createElement('div');
            tr.className = 'playlist-track';
            const img = document.createElement('img'); img.src = t.image || '/static/avatars/smile.png';
            const meta = document.createElement('div'); meta.className = 'track-meta';
            const title = document.createElement('div'); title.className = 'track-title'; title.textContent = t.name || 'Untitled';
            const art = document.createElement('div'); art.className = 'track-artist'; art.textContent = (t.artists || []).join(', ');
            meta.appendChild(title); meta.appendChild(art);
            const playBtn = document.createElement('button'); playBtn.textContent = '►';
            playBtn.addEventListener('click', () => {
                startPlayback(tracks, i);
                closeModal();
            });
            tr.appendChild(img); tr.appendChild(meta); tr.appendChild(playBtn);
            playlistTracksNode.appendChild(tr);
        });
    }

    function startPlayback(tracks, index) {
        if (!tracks || tracks.length === 0) return;
        currentPlaylist = tracks;
        currentTrackIndex = index || 0;
        const track = currentPlaylist[currentTrackIndex];
        if (!track) return;
        // prefer preview_url; if missing, fallback to opening external_url in new tab
        if (!track.preview_url) {
            window.open(track.external_url, '_blank');
            return;
        }
        audioPlayer.src = track.preview_url;
        audioPlayer.crossOrigin = 'anonymous';
        audioPlayer.play().catch(() => {});
        musicBar.style.display = 'flex';
        musicBarImage.src = track.image || '/static/avatars/smile.png';
        musicBarTitle.textContent = track.name || '';
        musicBarArtist.textContent = (track.artists || []).join(', ');
        musicBarPlay.textContent = '⏸';

        // progress timer
        if (progressTimer) clearInterval(progressTimer);
        progressTimer = setInterval(() => {
            if (!audioPlayer.duration || isNaN(audioPlayer.duration)) return;
            const pct = (audioPlayer.currentTime / audioPlayer.duration) * 100;
            musicBarProgressFill.style.width = `${pct}%`;
            playerProgressBar.style.width = `${pct}%`;
        }, 250);

        audioPlayer.onended = () => {
            musicBarPlay.textContent = '▶';
            // auto-advance
            if (currentTrackIndex < currentPlaylist.length - 1) {
                startPlayback(currentPlaylist, currentTrackIndex + 1);
            } else {
                if (progressTimer) clearInterval(progressTimer);
                musicBarProgressFill.style.width = '0%';
                playerProgressBar.style.width = '0%';
            }
        };
    }

    musicBarPlay.addEventListener('click', () => {
        if (audioPlayer.paused) {
            audioPlayer.play();
            musicBarPlay.textContent = '⏸';
        } else {
            audioPlayer.pause();
            musicBarPlay.textContent = '▶';
        }
    });

    playerPrevBtn.addEventListener('click', () => {
        if (currentTrackIndex > 0) startPlayback(currentPlaylist, currentTrackIndex - 1);
    });
    playerNextBtn.addEventListener('click', () => {
        if (currentTrackIndex < currentPlaylist.length - 1) startPlayback(currentPlaylist, currentTrackIndex + 1);
    });

    // click handler for playlist cards (delegate)
    document.addEventListener('click', (e) => {
        const el = e.target.closest('.playlist-card');
        if (!el) return;
        e.preventDefault();
        const href = el.getAttribute('href') || '';
        // the playlist id is not present directly in href; store data-id attribute when creating cards
        const pid = el.dataset.playlistId || (new URL(href, window.location.href).pathname.split('/').pop());
        const name = el.querySelector('.playlist-title') ? el.querySelector('.playlist-title').textContent : '';
        openPlaylistModal({ id: pid, name: name });
    });


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
            
            // Get microphone stream with relaxed constraints for compatibility
            stream = await navigator.mediaDevices.getUserMedia({ 
                audio: {
                    sampleRate: { ideal: 16000 },  // Prefer 16kHz, but don't require
                    channelCount: { ideal: 1 },    // Prefer mono, but don't require
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
    // Keep chat intact for current session; this resets ephemeral UI only
    if (transcriptionContainer) transcriptionContainer.style.display = "none";
    if (llmResponseContainer) llmResponseContainer.style.display = "none";
    if (transcriptionOutput) transcriptionOutput.textContent = "";
    if (llmResponseOutput) llmResponseOutput.textContent = "";
        statusDisplay.classList.remove('error');
        stopAudioBtn.style.display = 'none';
    }

    // Full UI reset when switching sessions: stop recording, stop playback, clear visuals and buffers
    function resetAllUI() {
        // Stop any ongoing recording
        try { if (isRecording) stopRecording(); } catch (e) {}
        // Disconnect any recording nodes
        try { disconnectRecording(); } catch (e) {}
        // Stop playback and clear audio element
        try {
            responseAudio.pause();
            responseAudio.currentTime = 0;
            responseAudio.src = '';
            responseAudio.style.display = 'none';
        } catch (e) {}
        try { disconnectPlayback(); } catch (e) {}
        // Clear UI elements
        try { chatContainer.innerHTML = ''; } catch (e) {}
        try { base64AudioChunks = []; } catch (e) {}
        murfFinalReceived = false;
        try { if (transcriptionOutput) transcriptionOutput.textContent = ''; } catch (e) {}
        try { if (llmResponseOutput) llmResponseOutput.textContent = ''; } catch (e) {}
        try { if (transcriptionContainer) transcriptionContainer.style.display = 'none'; } catch (e) {}
        try { if (llmResponseContainer) llmResponseContainer.style.display = 'none'; } catch (e) {}
        // Reset buttons and visual indicators
        try { updateUIRecording(false); } catch (e) {}
        try { stopPulseEffect(); } catch (e) {}
        try { websocketStatus.textContent = 'WebSocket: Disconnected'; websocketStatus.className = 'websocket-status'; } catch (e) {}
        try { transcriptionStatus.textContent = 'Transcription: Inactive'; transcriptionStatus.className = 'transcription-status'; } catch (e) {}
        try { stopAudioBtn.style.display = 'none'; } catch (e) {}
        try { statusDisplay.textContent = 'New session started.'; } catch (e) {}
    }

    async function loadChatHistory(sid) {
        try {
            const res = await fetch(`/agent/chat/history/${encodeURIComponent(sid)}`);
            if (!res.ok) return;
            const data = await res.json();
            if (data && data.history && Array.isArray(data.history)) {
                chatContainer.innerHTML = '';
                for (const msg of data.history) {
                    if (msg.role === 'user') {
                        const text = (msg.parts && msg.parts.join(' ')) || '';
                        appendUserBubble(text);
                    } else if (msg.role === 'model') {
                        const text = (msg.parts && msg.parts.join(' ')) || '';
                        appendAIBubbleStart();
                        appendAIBubbleChunk(text);
                        finalizeAIBubble(text);
                    }
                }
            }
        } catch (e) {
            // ignore
        }
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
            // show partial as a user bubble (only on end_of_turn commit)
            if (end_of_turn) {
                appendUserBubble(transcript);
            }
        } else {
            // no-op
        }
    }

    // --- LLM Streaming Functions ---
    let currentLLMResponse = "";
    let murfFinalReceived = false;
    // Flag to indicate we've started playback for the current LLM response
    let responsePlaying = false;
    // Fallback timer when Murf streaming doesn't produce a final audio
    let llmFallbackTimer = null;
    
    function handleLLMStart(data) {
        currentLLMResponse = "";
        murfFinalReceived = false;
        base64AudioChunks = [];
        // If the server provided the user's transcript, show it as a user bubble
        if (data && data.transcript) {
            appendUserBubble(data.transcript);
        }

        // Show the LLM response area via chat bubble
        appendAIBubbleStart();
        
        // Update status
        statusDisplay.textContent = "AI is thinking...";
        statusDisplay.classList.remove('error');
        
    // Scroll to show the LLM response area
    if (llmResponseContainer) llmResponseContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    
    function handleLLMChunk(data) {
    // Ensure container visible even if start event was missed
    if (llmResponseContainer) llmResponseContainer.style.display = "block";
        if (data.text) {
            currentLLMResponse += data.text;
            appendAIBubbleChunk(data.text);
        }
    }
    
    function handleLLMComplete(data) {
        // Ensure container visible even if start event was missed
        if (llmResponseContainer) llmResponseContainer.style.display = "block";

        // Finalize AI bubble and show final response text
        finalizeAIBubble(data.full_response || currentLLMResponse);

        // Update status
        statusDisplay.textContent = "AI response completed. Ready for next input.";

        // Trigger a subtle glow/spark on the agent container as a reward
        try {
            const agent = document.querySelector('.agent-container');
            if (agent) {
                agent.classList.remove('spark');
                // force reflow to restart animation
                // eslint-disable-next-line no-unused-expressions
                void agent.offsetWidth;
                agent.classList.add('spark');
                // remove the class after animation completes (1.6s)
                setTimeout(() => agent.classList.remove('spark'), 1600);
            }
        } catch (e) {}

        // Add completion styling if element present
        if (llmResponseOutput && llmResponseOutput.classList) {
            llmResponseOutput.classList.add('completed');
        }
        // Fallback: if Murf didn't signal final but we have chunks, play them
        if (!murfFinalReceived && base64AudioChunks && base64AudioChunks.length > 0) {
            try { setTimeout(() => playSavedChunks(), 200); } catch(_) {}
        }

        // If Murf doesn't respond with a final audio within a short window, request
        // a server-side TTS of the full LLM response to guarantee playback.
        try {
            if (llmFallbackTimer) { clearTimeout(llmFallbackTimer); llmFallbackTimer = null; }
            const fullText = (data && data.full_response) ? data.full_response : currentLLMResponse;
            // wait a bit for Murf to finish; if still nothing, call /tts
            llmFallbackTimer = setTimeout(() => {
                if (responsePlaying || murfFinalReceived) return;
                if (!fullText || fullText.trim().length === 0) return;
                // if we already collected chunks, prefer to play them (will be handled above)
                if (base64AudioChunks && base64AudioChunks.length > 0) {
                    try { playSavedChunks(); return; } catch (_) { }
                }
                // otherwise, request server TTS for the complete response
                fetch('/tts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: fullText })
                }).then(r => r.json()).then(d => {
                    if (d && d.audio_url) {
                        // start playback via existing helper
                        responsePlaying = true;
                        playResponseAudio(d.audio_url).finally(() => { responsePlaying = false; });
                    }
                }).catch(err => {
                    console.error('Fallback TTS request failed', err);
                });
            }, 1200);
        } catch (e) {}
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
            // strip WAV header if present ("RIFF")
            if (bytes.length > 44 && bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46) {
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
            // prevent fallback while playing
            responsePlaying = true;
            if (llmFallbackTimer) { clearTimeout(llmFallbackTimer); llmFallbackTimer = null; }
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
            // clear stored chunks after playback
            base64AudioChunks = [];
            responsePlaying = false;
            disconnectPlayback();
        };
    }

    function playSavedChunks() {
        const saved = base64AudioChunks;
        if (saved && saved.length > 0) {
            // prevent multiple concurrent plays
            if (responsePlaying) return;
            playCombinedWavChunks(saved);
            // clear stored chunks after starting playback (safety)
            base64AudioChunks = [];
        }
    }
    // ------------------ END WAV Assembly -----------------
    
    function handleLLMError(data) {
        console.error('LLM error:', data.error);
        
        // Show error in LLM response area
        if (llmResponseContainer) llmResponseContainer.style.display = "block";
        if (llmResponseOutput) {
            llmResponseOutput.textContent = `Error: ${data.error}`;
            if (llmResponseOutput.classList) llmResponseOutput.classList.add('error');
        }
        
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


document.addEventListener('DOMContentLoaded', () => {
    console.log('DOMContentLoaded event fired');
    // Initialize all the functions that need DOM elements
    // The setupConfigButton() is already called above
});

// --- End of script.js ---
// This script handles audio recording, playback, and UI updates for the application.