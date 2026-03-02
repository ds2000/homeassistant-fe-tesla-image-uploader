// Tesla Card Image Uploader — form logic & API calls
// Vanilla JS, no frameworks

(function () {
    'use strict';

    // ── Config ──────────────────────────────────────────────────────────

    var GITHUB_PAT = 'YOUR_GITHUB_PAT_HERE'; // actions:write only — safe to commit
    var GITHUB_UPLOAD_PAT = 'YOUR_GITHUB_UPLOAD_PAT_HERE'; // contents:write scope
    var REPO = 'ds2000/homeassistant-fe-tesla-image-uploader';
    var WORKFLOW_FILE = 'send-verification.yml';
    var PUBLIC_HMAC_SALT = 'tesla-card-uploader-hmac-v1';

    // ── Screenshot layers ─────────────────────────────────────────────
    // Two groups: unplugged (off-charge) then on-charge (plugged in).
    // The `section` property marks the start of a new group in the sidebar.
    // Files are uploaded into separate subdirectories per group.

    var LAYERS = [
        // ── Unplugged ────────────────────────────────────────────────
        { key: 'closed', filename: 'closed.png', label: 'All Closed', section: 'Unplugged',
          description: 'Side view with every door, trunk, and frunk fully closed.',
          instruction: { title: 'Base image \u2014 all closed', steps: [
            'Unplug the charger if connected',
            'Open the Tesla app \u2192 scroll down to Controls',
            'Ensure ALL doors, trunk, frunk and charge port are closed',
            'Wait for any animation to finish completely, then screenshot'
          ] } },
        { key: 'chargeport', filename: 'cp.png', label: 'Charge Port',
          description: 'Side view with only the charge port door open.',
          instruction: { title: 'Charge port open', steps: [
            'Open the Tesla app \u2192 Controls',
            'Tap the charge port button to open it',
            'Wait for the charge port animation to finish',
            'Take a screenshot once the port is fully open and still'
          ] } },
        { key: 'frunk', filename: 'cp_ft.png', label: 'Frunk Open',
          description: 'Side view with the front trunk (frunk) open.',
          instruction: { title: 'Frunk open', steps: [
            'Open the Tesla app \u2192 Controls',
            'Tap Frunk to open the front trunk',
            'Wait for the frunk to reach its fully-open position \u2014 do NOT screenshot mid-swing',
            'Take a screenshot once the frunk is completely still'
          ] } },
        { key: 'trunk', filename: 'rt.png', label: 'Trunk Open',
          description: 'Side view with the rear trunk (boot) open.',
          instruction: { title: 'Trunk open', steps: [
            'Open the Tesla app \u2192 Controls',
            'Tap Trunk to open the rear trunk',
            'Wait for the trunk to reach its fully-open position \u2014 the silhouette changes',
            'Take a screenshot once the trunk is completely still'
          ] } },
        { key: 'front_doors', filename: 'front_doors.png', label: 'Both Front Doors',
          description: 'Side view with BOTH front doors open simultaneously.',
          instruction: { title: 'Both front doors open', steps: [
            'Open the Tesla app \u2192 Controls',
            'Open BOTH front doors (driver and passenger side) simultaneously',
            'Wait for both door-open animations to finish completely',
            'Take a screenshot \u2014 both front doors should be fully open'
          ] } },
        { key: 'rear_doors', filename: 'rear_doors.png', label: 'Both Rear Doors',
          description: 'Side view with BOTH rear doors open simultaneously.',
          instruction: { title: 'Both rear doors open', steps: [
            'Open the Tesla app \u2192 Controls',
            'Close the front doors, then open BOTH rear doors (driver and passenger side)',
            'Wait for both door-open animations to finish completely',
            'Take a screenshot \u2014 both rear doors should be fully open'
          ] } },
        { key: 'controls', filename: 'top_controls.png', label: 'Controls Panel',
          description: 'Top-down car view from the Controls screen.',
          instruction: { title: 'Controls panel \u2014 top-down view', steps: [
            'Open the Tesla app \u2192 Controls',
            'Tap anywhere on the car image to enter the top-down view',
            'Ensure all doors and trunk are closed',
            'Take a screenshot of the full top-down car image'
          ] } },
        { key: 'climate', filename: 'top_climate.png', label: 'Climate Panel',
          description: 'Top-down interior view from the Climate screen.',
          instruction: { title: 'Climate panel \u2014 top-down view', steps: [
            'Open the Tesla app \u2192 Climate',
            'Turn OFF the climate system (tap the power button) \u2014 seat heater icons must be hidden',
            'Wait for the UI to settle \u2014 no spinning fans or active indicators',
            'Take a screenshot of the full top-down interior view'
          ] } },

        // ── On charge ────────────────────────────────────────────────
        { key: 'oc_closed', filename: 'oc_closed.png', label: 'All Closed', section: 'On Charge',
          description: 'Side view while charging \u2014 cable plugged in, all doors closed.',
          instruction: { title: 'Charging base \u2014 all closed', steps: [
            'Plug in the charger and start a charging session',
            'Open the Tesla app \u2192 scroll down to Controls',
            'Ensure ALL doors, trunk and frunk are closed',
            'Wait for any animation to finish \u2014 the charging cable should be visible, then screenshot'
          ] } },
        { key: 'oc_frunk', filename: 'oc_cp_ft.png', label: 'Frunk Open',
          description: 'Side view while charging with the frunk open.',
          instruction: { title: 'Charging \u2014 frunk open', steps: [
            'Keep the charger plugged in',
            'Open the Tesla app \u2192 Controls \u2192 tap Frunk',
            'Wait for the frunk to reach its fully-open position',
            'Take a screenshot once the frunk is completely still'
          ] } },
        { key: 'oc_trunk', filename: 'oc_rt.png', label: 'Trunk Open',
          description: 'Side view while charging with the trunk open.',
          instruction: { title: 'Charging \u2014 trunk open', steps: [
            'Keep the charger plugged in',
            'Open the Tesla app \u2192 Controls \u2192 tap Trunk',
            'Wait for the trunk to reach its fully-open position',
            'Take a screenshot once the trunk is completely still'
          ] } },
        { key: 'oc_front_doors', filename: 'oc_front_doors.png', label: 'Both Front Doors',
          description: 'Side view while charging with BOTH front doors open simultaneously.',
          instruction: { title: 'Charging \u2014 both front doors', steps: [
            'Keep the charger plugged in',
            'Open BOTH front doors (driver and passenger side) simultaneously',
            'Wait for both door-open animations to finish completely',
            'Take a screenshot \u2014 cable and both open doors should be visible'
          ] } },
        { key: 'oc_rear_doors', filename: 'oc_rear_doors.png', label: 'Both Rear Doors',
          description: 'Side view while charging with BOTH rear doors open simultaneously.',
          instruction: { title: 'Charging \u2014 both rear doors', steps: [
            'Keep the charger plugged in',
            'Close the front doors, then open BOTH rear doors (driver and passenger side)',
            'Wait for both door-open animations to finish completely',
            'Take a screenshot \u2014 cable and both open doors should be visible'
          ] } },
        { key: 'oc_controls', filename: 'oc_top_controls.png', label: 'Controls Panel',
          description: 'Top-down car view from the Controls screen while charging.',
          instruction: { title: 'Charging \u2014 controls panel', steps: [
            'Keep the charger plugged in',
            'Open the Tesla app \u2192 Controls \u2192 tap the car for top-down view',
            'Ensure all doors and trunk are closed',
            'Take a screenshot \u2014 the charging cable should be visible'
          ] } },
        { key: 'oc_climate', filename: 'oc_top_climate.png', label: 'Climate Panel',
          description: 'Top-down interior view from the Climate screen while charging.',
          instruction: { title: 'Charging \u2014 climate panel', steps: [
            'Keep the charger plugged in',
            'Open the Tesla app \u2192 Climate',
            'Turn OFF the climate system \u2014 seat heater icons must be hidden',
            'Take a screenshot of the full top-down interior view'
          ] } }
    ];

    // ── DOM refs ────────────────────────────────────────────────────────

    var $model = document.getElementById('select-model');
    var $year = document.getElementById('select-year');
    var $colour = document.getElementById('select-colour');
    var $colourStatus = document.getElementById('colour-status-msg');
    var $btnStep2 = document.getElementById('btn-to-step2');

    var $step1 = document.getElementById('step-1');
    var $step2 = document.getElementById('step-2');
    var $step3 = document.getElementById('step-3');

    var $verifyCombo = document.getElementById('verify-combo-label');
    var $verifyForm = document.getElementById('verify-form');
    var $verifySent = document.getElementById('verify-sent');
    var $inputEmail = document.getElementById('input-email');
    var $btnSend = document.getElementById('btn-send-verify');
    var $verifySentEmail = document.getElementById('verify-sent-email');
    var $btnResend = document.getElementById('btn-resend');
    var $btnBack1 = document.getElementById('btn-back-to-step1');
    var $btnDevSkip = document.getElementById('btn-dev-skip');

    var $uploadCombo = document.getElementById('upload-combo-label');
    var $uploadWizard = document.getElementById('upload-wizard');
    var $uploadSidebar = document.getElementById('upload-sidebar-list');
    var $uploadGuide = document.getElementById('upload-guide');
    var $uploadInstructions = document.getElementById('upload-instructions');
    var $uploadDropzoneArea = document.getElementById('upload-dropzone-area');
    var $btnUploadPrev = document.getElementById('btn-upload-prev');
    var $btnUploadNext = document.getElementById('btn-upload-next');
    var $uploadNavIndicator = document.getElementById('upload-nav-indicator');
    var $uploadCounter = document.getElementById('upload-counter');
    var $btnSubmit = document.getElementById('btn-submit-upload');
    var $uploadProgress = document.getElementById('upload-progress');
    var $uploadProgressText = document.getElementById('upload-progress-text');
    var $uploadProgressBar = document.getElementById('upload-progress-bar');
    var $uploadSuccess = document.getElementById('upload-success');
    var $uploadSuccessLink = document.getElementById('upload-success-link');
    var $uploadError = document.getElementById('upload-error');
    var $btnBack1From3 = document.getElementById('btn-back-to-step1-from3');

    var stepperSteps = document.querySelectorAll('.stepper-step');

    // ── State ───────────────────────────────────────────────────────────

    var statusData = null;
    var selection = { model: '', year: '', colour: '' };
    var verificationToken = '';
    var uploadedFiles = {};
    var currentLayerIndex = 0;
    var layerDOMCache = {};

    // ── Helpers ─────────────────────────────────────────────────────────

    function formatColourName(key) {
        return key.replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
    }

    function comboLabel() {
        if (!statusData || !selection.model || !selection.year || !selection.colour) return '';
        var m = statusData.models[selection.model];
        var y = m.years[selection.year];
        return m.label + ' ' + y.label + ' — ' + formatColourName(selection.colour);
    }

    function setStep(n) {
        $step1.hidden = n !== 1;
        $step2.hidden = n !== 2;
        $step3.hidden = n !== 3;
        stepperSteps.forEach(function (el) {
            var s = parseInt(el.getAttribute('data-step'), 10);
            el.classList.toggle('active', s === n);
            el.classList.toggle('done', s < n);
        });
        document.querySelector('.container').classList.toggle('container-wide', n === 3);
        if (n === 3) initUploadWizard();
    }

    // ── Crypto helpers (Web Crypto API) ────────────────────────────────

    function getHmacKey() {
        var enc = new TextEncoder();
        return crypto.subtle.importKey(
            'raw',
            enc.encode(PUBLIC_HMAC_SALT),
            { name: 'HMAC', hash: 'SHA-256' },
            false,
            ['sign', 'verify']
        );
    }

    function computeSignature(token, model, year, colour) {
        var message = token + model + year + colour;
        var enc = new TextEncoder();
        return getHmacKey().then(function (key) {
            return crypto.subtle.sign('HMAC', key, enc.encode(message));
        }).then(function (buf) {
            return Array.from(new Uint8Array(buf)).map(function (b) {
                return b.toString(16).padStart(2, '0');
            }).join('');
        });
    }

    function verifySignature(token, model, year, colour, sig) {
        return computeSignature(token, model, year, colour).then(function (expected) {
            return expected === sig;
        });
    }

    // ── Populate dropdowns ─────────────────────────────────────────────

    function populateModels() {
        $model.innerHTML = '<option value="">Choose a model</option>';
        var models = statusData.models;
        Object.keys(models).forEach(function (key) {
            var opt = document.createElement('option');
            opt.value = key;
            opt.textContent = models[key].label;
            $model.appendChild(opt);
        });
        $model.disabled = false;
    }

    function populateYears() {
        $year.innerHTML = '';
        $colour.innerHTML = '<option value="">Select a year range first</option>';
        $colour.disabled = true;
        $colourStatus.hidden = true;
        $btnStep2.disabled = true;
        selection.year = '';
        selection.colour = '';

        if (!selection.model) {
            $year.innerHTML = '<option value="">Select a model first</option>';
            $year.disabled = true;
            return;
        }

        var years = statusData.models[selection.model].years;
        $year.innerHTML = '<option value="">Choose a year range</option>';
        Object.keys(years).forEach(function (key) {
            var opt = document.createElement('option');
            opt.value = key;
            opt.textContent = years[key].label;
            $year.appendChild(opt);
        });
        $year.disabled = false;
    }

    function populateColours() {
        $colour.innerHTML = '';
        $colourStatus.hidden = true;
        $btnStep2.disabled = true;
        selection.colour = '';

        if (!selection.year) {
            $colour.innerHTML = '<option value="">Select a year range first</option>';
            $colour.disabled = true;
            return;
        }

        var colours = statusData.models[selection.model].years[selection.year].colours;
        $colour.innerHTML = '<option value="">Choose a colour</option>';
        Object.keys(colours).forEach(function (key) {
            var entry = colours[key];
            var opt = document.createElement('option');
            opt.value = key;

            var label = formatColourName(key);
            if (entry.status === 'pending') {
                label += ' — Under review';
                opt.disabled = true;
            } else if (entry.status === 'complete') {
                label += ' — Already available';
                opt.disabled = true;
            }
            opt.textContent = label;
            $colour.appendChild(opt);
        });
        $colour.disabled = false;
    }

    function onColourChange() {
        selection.colour = $colour.value;
        $colourStatus.hidden = true;
        $btnStep2.disabled = true;

        if (!selection.colour) return;

        var entry = statusData.models[selection.model].years[selection.year].colours[selection.colour];
        if (entry.status === 'pending') {
            $colourStatus.className = 'status-msg pending';
            $colourStatus.textContent = 'This combination is currently under review (PR #' + entry.pr + '). Please choose another.';
            $colourStatus.hidden = false;
        } else if (entry.status === 'complete') {
            $colourStatus.className = 'status-msg complete';
            $colourStatus.textContent = 'This combination is already available in the card. Please choose another.';
            $colourStatus.hidden = false;
        } else {
            $btnStep2.disabled = false;
        }
    }

    // ── Email verification ─────────────────────────────────────────────

    function showVerifyForm() {
        $verifyForm.hidden = false;
        $verifySent.hidden = true;
        $inputEmail.value = '';
        $btnSend.disabled = true;
        $btnSend.textContent = 'Send verification email';
    }

    function sendVerification() {
        var email = $inputEmail.value.trim();
        if (!email) return;

        $btnSend.disabled = true;
        $btnSend.innerHTML = '<span class="spinner"></span>Sending...';

        verificationToken = crypto.randomUUID();

        computeSignature(verificationToken, selection.model, selection.year, selection.colour)
            .then(function (sig) {
                // Store in sessionStorage so we can recover on page refresh
                sessionStorage.setItem('verify_token', verificationToken);
                sessionStorage.setItem('verify_sig', sig);
                sessionStorage.setItem('verify_model', selection.model);
                sessionStorage.setItem('verify_year', selection.year);
                sessionStorage.setItem('verify_colour', selection.colour);

                var tokenHash = verificationToken + ':' + sig;

                return fetch(
                    'https://api.github.com/repos/' + REPO + '/actions/workflows/' + WORKFLOW_FILE + '/dispatches',
                    {
                        method: 'POST',
                        headers: {
                            'Authorization': 'Bearer ' + GITHUB_PAT,
                            'Accept': 'application/vnd.github+json',
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            ref: 'main',
                            inputs: {
                                email: email,
                                token_hash: tokenHash,
                                model: selection.model,
                                year_range: selection.year,
                                colour: selection.colour
                            }
                        })
                    }
                );
            })
            .then(function (resp) {
                if (!resp.ok) throw new Error('GitHub API returned ' + resp.status);

                $verifyForm.hidden = true;
                $verifySent.hidden = false;
                $verifySentEmail.textContent = email;
            })
            .catch(function (err) {
                $btnSend.textContent = 'Send verification email';
                $btnSend.disabled = false;

                var errEl = document.querySelector('#verify-form .error-text');
                if (!errEl) {
                    errEl = document.createElement('p');
                    errEl.className = 'error-text';
                    $verifyForm.appendChild(errEl);
                }
                errEl.textContent = 'Failed to send verification email. ' + err.message;
            });
    }

    // ── Verification callback (user clicked email link) ────────────────

    function checkVerificationCallback() {
        var params = new URLSearchParams(window.location.search);
        var token = params.get('token');
        var sig = params.get('sig');
        var model = params.get('model');
        var year = params.get('year');
        var colour = params.get('colour');

        if (!token || !sig || !model || !year || !colour) return false;

        // Clean URL without reloading
        window.history.replaceState({}, '', window.location.pathname);

        verifySignature(token, model, year, colour, sig).then(function (valid) {
            if (!valid) {
                alert('Invalid verification link. The signature does not match. Please request a new verification email.');
                return;
            }

            // Store verified state in sessionStorage
            sessionStorage.setItem('verified', 'true');
            sessionStorage.setItem('verified_model', model);
            sessionStorage.setItem('verified_year', year);
            sessionStorage.setItem('verified_colour', colour);

            // Apply selection and go to step 3
            selection.model = model;
            selection.year = year;
            selection.colour = colour;
            $uploadCombo.textContent = comboLabel();
            setStep(3);
        });

        return true;
    }

    function checkSessionVerification() {
        if (sessionStorage.getItem('verified') !== 'true') return false;

        var model = sessionStorage.getItem('verified_model');
        var year = sessionStorage.getItem('verified_year');
        var colour = sessionStorage.getItem('verified_colour');

        if (!model || !year || !colour) return false;
        if (!statusData.models[model]) return false;
        if (!statusData.models[model].years[year]) return false;
        if (!statusData.models[model].years[year].colours[colour]) return false;

        selection.model = model;
        selection.year = year;
        selection.colour = colour;
        $uploadCombo.textContent = comboLabel();
        setStep(3);
        return true;
    }

    // ── Upload: wizard rendering ────────────────────────────────────────

    function initUploadWizard() {
        resetUploadState();
        currentLayerIndex = 0;

        // Build sidebar list with section headers
        $uploadSidebar.innerHTML = '';
        var sectionCounter = 0;
        LAYERS.forEach(function (layer, idx) {
            // Insert section header when a new section begins
            if (layer.section) {
                sectionCounter = 0;
                var header = document.createElement('li');
                header.className = 'upload-sidebar-header';
                header.textContent = layer.section;
                $uploadSidebar.appendChild(header);
            }
            sectionCounter++;

            var li = document.createElement('li');
            li.className = 'upload-sidebar-item';
            li.dataset.index = idx;
            li.dataset.sectionNum = sectionCounter;

            var num = document.createElement('span');
            num.className = 'upload-sidebar-num';
            num.textContent = uploadedFiles[layer.key] ? '\u2713' : sectionCounter;

            var label = document.createElement('span');
            label.className = 'upload-sidebar-label';
            label.textContent = layer.label;

            li.appendChild(num);
            li.appendChild(label);

            if (uploadedFiles[layer.key]) li.classList.add('done');

            li.addEventListener('click', function () {
                goToLayer(idx);
            });

            $uploadSidebar.appendChild(li);
        });

        renderCurrentLayer();
        updateUploadCounter();
    }

    function renderCurrentLayer() {
        var layer = LAYERS[currentLayerIndex];

        // Update sidebar active state
        var items = $uploadSidebar.querySelectorAll('.upload-sidebar-item');
        var activeItem = null;
        items.forEach(function (item) {
            var idx = parseInt(item.dataset.index, 10);
            var isActive = idx === currentLayerIndex;
            item.classList.toggle('active', isActive);
            if (isActive) activeItem = item;
        });

        // Scroll active sidebar item into view on mobile
        if (activeItem) {
            activeItem.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
        }

        // Load SVG guide
        loadGuide(layer.key);

        // Render instructions
        renderInstructions(layer);

        // Render or restore dropzone
        renderLayerDropzone(layer);

        // Update nav
        $uploadNavIndicator.textContent = (currentLayerIndex + 1) + ' / ' + LAYERS.length;
        $btnUploadPrev.disabled = currentLayerIndex === 0;

        if (currentLayerIndex === LAYERS.length - 1) {
            $btnUploadNext.textContent = 'Review';
        } else {
            $btnUploadNext.textContent = 'Next';
        }
    }

    function loadGuide(layerKey) {
        $uploadGuide.innerHTML = '';
        var guidePath = 'assets/guides/' + layerKey + '.png';

        var img = document.createElement('img');
        img.className = 'upload-guide-img';
        img.alt = 'Guide: ' + layerKey;
        img.src = guidePath;

        img.addEventListener('error', function () {
            if (LAYERS[currentLayerIndex].key === layerKey) {
                var fallback = document.createElement('div');
                fallback.className = 'upload-guide-fallback';
                fallback.textContent = LAYERS.filter(function (l) { return l.key === layerKey; })[0].label;
                $uploadGuide.innerHTML = '';
                $uploadGuide.appendChild(fallback);
            }
        });

        if (LAYERS[currentLayerIndex].key === layerKey) {
            $uploadGuide.appendChild(img);
        }
    }

    function renderInstructions(layer) {
        $uploadInstructions.innerHTML = '';

        var title = document.createElement('div');
        title.className = 'upload-instructions-title';
        title.textContent = layer.instruction.title;

        var ol = document.createElement('ol');
        ol.className = 'upload-instructions-steps';
        layer.instruction.steps.forEach(function (step) {
            var li = document.createElement('li');
            li.textContent = step;
            ol.appendChild(li);
        });

        $uploadInstructions.appendChild(title);
        $uploadInstructions.appendChild(ol);
    }

    function renderLayerDropzone(layer) {
        $uploadDropzoneArea.innerHTML = '';

        // Check cache first
        if (layerDOMCache[layer.key]) {
            $uploadDropzoneArea.appendChild(layerDOMCache[layer.key]);
            return;
        }

        // Build new dropzone
        var dropzone = document.createElement('div');
        dropzone.className = 'upload-dropzone';
        if (uploadedFiles[layer.key]) dropzone.classList.add('filled');

        var fileInput = document.createElement('input');
        fileInput.type = 'file';
        fileInput.accept = 'image/png';
        fileInput.hidden = true;

        var emptyState = document.createElement('div');
        emptyState.className = 'upload-dropzone-empty';

        var fallbackIcon = document.createElement('div');
        fallbackIcon.className = 'upload-dropzone-icon';
        fallbackIcon.textContent = '\u2191';

        var overlayText = document.createElement('div');
        overlayText.className = 'upload-dropzone-text';
        overlayText.textContent = 'Drop PNG or click to upload';

        emptyState.appendChild(fallbackIcon);
        emptyState.appendChild(overlayText);

        var thumbWrap = document.createElement('div');
        thumbWrap.className = 'upload-thumb-wrap';
        thumbWrap.hidden = true;

        var thumb = document.createElement('img');
        thumb.className = 'upload-thumb';
        thumb.alt = layer.filename + ' preview';

        var removeBtn = document.createElement('button');
        removeBtn.className = 'upload-remove';
        removeBtn.title = 'Remove';
        removeBtn.type = 'button';
        removeBtn.textContent = '\u2715';

        thumbWrap.appendChild(thumb);
        thumbWrap.appendChild(removeBtn);

        dropzone.appendChild(fileInput);
        dropzone.appendChild(emptyState);
        dropzone.appendChild(thumbWrap);

        // If already uploaded, show thumbnail
        if (uploadedFiles[layer.key]) {
            var url = URL.createObjectURL(uploadedFiles[layer.key]);
            thumb.src = url;
            thumb.dataset.objectUrl = url;
            emptyState.hidden = true;
            thumbWrap.hidden = false;
        }

        // Click to upload
        dropzone.addEventListener('click', function (e) {
            if (e.target.closest('.upload-remove')) return;
            fileInput.click();
        });

        fileInput.addEventListener('change', function () {
            if (fileInput.files.length) {
                handleFileWizard(layer.key, fileInput.files[0], dropzone, emptyState, thumbWrap, thumb);
            }
        });

        // Drag and drop
        dropzone.addEventListener('dragover', function (e) {
            e.preventDefault();
            dropzone.classList.add('dragover');
        });

        dropzone.addEventListener('dragleave', function () {
            dropzone.classList.remove('dragover');
        });

        dropzone.addEventListener('drop', function (e) {
            e.preventDefault();
            dropzone.classList.remove('dragover');
            if (e.dataTransfer.files.length) {
                handleFileWizard(layer.key, e.dataTransfer.files[0], dropzone, emptyState, thumbWrap, thumb);
            }
        });

        // Remove
        removeBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            removeFileWizard(layer.key, dropzone, emptyState, thumbWrap, thumb, fileInput);
        });

        layerDOMCache[layer.key] = dropzone;
        $uploadDropzoneArea.appendChild(dropzone);
    }

    function goToLayer(index) {
        if (index < 0 || index >= LAYERS.length) return;
        currentLayerIndex = index;
        renderCurrentLayer();
    }

    function updateSidebarStatus() {
        var items = $uploadSidebar.querySelectorAll('.upload-sidebar-item');
        items.forEach(function (item) {
            var idx = parseInt(item.dataset.index, 10);
            var layer = LAYERS[idx];
            var done = !!uploadedFiles[layer.key];
            item.classList.toggle('done', done);
            var num = item.querySelector('.upload-sidebar-num');
            num.textContent = done ? '\u2713' : item.dataset.sectionNum;
        });
    }

    // ── Upload: file handling & validation ───────────────────────────────

    function validatePng(file) {
        return new Promise(function (resolve) {
            var reader = new FileReader();
            reader.onload = function () {
                var arr = new Uint8Array(reader.result);
                if (arr[0] !== 0x89 || arr[1] !== 0x50 || arr[2] !== 0x4E || arr[3] !== 0x47) {
                    resolve({ valid: false, reason: 'Not a valid PNG file. Please take a screenshot (not a photo).' });
                    return;
                }
                resolve({ valid: true });
            };
            reader.onerror = function () {
                resolve({ valid: false, reason: 'Could not read file.' });
            };
            reader.readAsArrayBuffer(file.slice(0, 30));
        });
    }

    function handleFileWizard(layerKey, file, dropzone, emptyState, thumbWrap, thumb) {
        if (file.type !== 'image/png') {
            showUploadError('Only PNG files are accepted. "' + file.name + '" is not a PNG.');
            return;
        }

        validatePng(file).then(function (result) {
            if (!result.valid) {
                showUploadError(file.name + ': ' + result.reason);
                return;
            }

            hideUploadError();
            uploadedFiles[layerKey] = file;

            if (thumb.dataset.objectUrl) {
                URL.revokeObjectURL(thumb.dataset.objectUrl);
            }
            var url = URL.createObjectURL(file);
            thumb.src = url;
            thumb.dataset.objectUrl = url;

            emptyState.hidden = true;
            thumbWrap.hidden = false;
            dropzone.classList.add('filled');

            updateSidebarStatus();
            updateUploadCounter();
        });
    }

    function removeFileWizard(layerKey, dropzone, emptyState, thumbWrap, thumb, fileInput) {
        delete uploadedFiles[layerKey];

        if (thumb.dataset.objectUrl) {
            URL.revokeObjectURL(thumb.dataset.objectUrl);
            thumb.dataset.objectUrl = '';
        }
        thumb.src = '';

        thumbWrap.hidden = true;
        emptyState.hidden = false;
        dropzone.classList.remove('filled');
        fileInput.value = '';

        updateSidebarStatus();
        updateUploadCounter();
    }

    function updateUploadCounter() {
        var count = Object.keys(uploadedFiles).length;
        $uploadCounter.textContent = count + ' of ' + LAYERS.length + ' screenshots uploaded';
        $btnSubmit.disabled = count < LAYERS.length;
    }

    // ── Upload: progress & state helpers ─────────────────────────────────

    function setUploadProgress(text, pct) {
        $uploadProgress.hidden = false;
        $uploadProgressText.textContent = text;
        $uploadProgressBar.style.width = pct + '%';
        $btnSubmit.disabled = true;
        $btnBack1From3.disabled = true;
    }

    function hideUploadProgress() {
        $uploadProgress.hidden = true;
        $uploadProgressBar.style.width = '0%';
        $btnBack1From3.disabled = false;
        updateUploadCounter();
    }

    function showUploadError(msg) {
        $uploadError.textContent = msg;
        $uploadError.hidden = false;
    }

    function hideUploadError() {
        $uploadError.hidden = true;
    }

    function showUploadSuccess(branchName) {
        $uploadSuccess.hidden = false;
        $uploadSuccessLink.href = 'https://github.com/' + REPO + '/tree/' + encodeURIComponent(branchName);
        $btnSubmit.hidden = true;
        $uploadWizard.style.opacity = '0.5';
        $uploadWizard.style.pointerEvents = 'none';
        $btnBack1From3.disabled = false;
    }

    function resetUploadState() {
        uploadedFiles = {};
        layerDOMCache = {};
        currentLayerIndex = 0;
        $uploadSidebar.innerHTML = '';
        $uploadGuide.innerHTML = '';
        $uploadInstructions.innerHTML = '';
        $uploadDropzoneArea.innerHTML = '';
        $uploadSuccess.hidden = true;
        $uploadProgress.hidden = true;
        $uploadError.hidden = true;
        $btnSubmit.hidden = false;
        $btnSubmit.disabled = true;
        $uploadWizard.style.opacity = '';
        $uploadWizard.style.pointerEvents = '';
    }

    // ── Upload: GitHub API helpers ───────────────────────────────────────

    function ghApi(method, path, body) {
        var opts = {
            method: method,
            headers: {
                'Authorization': 'Bearer ' + GITHUB_UPLOAD_PAT,
                'Accept': 'application/vnd.github+json'
            }
        };
        if (body) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        return fetch('https://api.github.com' + path, opts).then(function (resp) {
            if (!resp.ok) {
                return resp.text().then(function (text) {
                    throw new Error('GitHub API ' + resp.status + ': ' + text);
                });
            }
            if (resp.status === 204) return null;
            return resp.json();
        });
    }

    function fileToBase64(file) {
        return new Promise(function (resolve, reject) {
            var reader = new FileReader();
            reader.onload = function () {
                resolve(reader.result.split(',')[1]);
            };
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    }

    // ── Upload: submit flow ──────────────────────────────────────────────

    async function submitToGitHub() {
        var model = selection.model;
        var year = selection.year;
        var colour = selection.colour;
        var shortToken = Array.from(crypto.getRandomValues(new Uint8Array(2)))
            .map(function (b) { return b.toString(16).padStart(2, '0'); }).join('');
        var branchName = 'submissions/' + model + '-' + year + '-' + colour + '-' + shortToken;
        var dirPath = 'submissions/' + model + '-' + year + '-' + colour + '/';

        try {
            hideUploadError();
            setUploadProgress('Getting repository info...', 0);

            // 1. Get main branch HEAD SHA
            var mainRef = await ghApi('GET', '/repos/' + REPO + '/git/ref/heads/main');
            var headSha = mainRef.object.sha;

            // 2. Get base tree SHA from the HEAD commit
            var headCommit = await ghApi('GET', '/repos/' + REPO + '/git/commits/' + headSha);
            var baseTreeSha = headCommit.tree.sha;

            // 3. Create blobs for all files
            var treeItems = [];
            for (var i = 0; i < LAYERS.length; i++) {
                var pct = 5 + Math.round((i / LAYERS.length) * 75);
                setUploadProgress('Uploading ' + LAYERS[i].filename + ' (' + (i + 1) + '/' + LAYERS.length + ')...', pct);
                var base64 = await fileToBase64(uploadedFiles[LAYERS[i].key]);
                var blob = await ghApi('POST', '/repos/' + REPO + '/git/blobs', {
                    content: base64,
                    encoding: 'base64'
                });
                treeItems.push({
                    path: dirPath + LAYERS[i].filename,
                    mode: '100644',
                    type: 'blob',
                    sha: blob.sha
                });
            }

            // 4. Create tree
            setUploadProgress('Creating file tree...', 85);
            var tree = await ghApi('POST', '/repos/' + REPO + '/git/trees', {
                base_tree: baseTreeSha,
                tree: treeItems
            });

            // 5. Create commit
            setUploadProgress('Creating commit...', 92);
            var commit = await ghApi('POST', '/repos/' + REPO + '/git/commits', {
                message: 'submission: ' + model + ' ' + year + ' ' + formatColourName(colour),
                tree: tree.sha,
                parents: [headSha]
            });

            // 6. Create branch
            setUploadProgress('Creating branch...', 97);
            await ghApi('POST', '/repos/' + REPO + '/git/refs', {
                ref: 'refs/heads/' + branchName,
                sha: commit.sha
            });

            // Done
            setUploadProgress('Done!', 100);
            showUploadSuccess(branchName);

        } catch (err) {
            hideUploadProgress();
            showUploadError('Upload failed: ' + err.message);
        }
    }

    // ── Event listeners ────────────────────────────────────────────────

    $model.addEventListener('change', function () {
        selection.model = $model.value;
        populateYears();
    });

    $year.addEventListener('change', function () {
        selection.year = $year.value;
        populateColours();
    });

    $colour.addEventListener('change', onColourChange);

    $btnStep2.addEventListener('click', function () {
        $verifyCombo.textContent = comboLabel();
        showVerifyForm();
        setStep(2);
    });

    $inputEmail.addEventListener('input', function () {
        $btnSend.disabled = !$inputEmail.validity.valid || !$inputEmail.value.trim();
    });

    $btnSend.addEventListener('click', sendVerification);

    $btnResend.addEventListener('click', function () {
        showVerifyForm();
        $inputEmail.focus();
    });

    $btnBack1.addEventListener('click', function () {
        setStep(1);
    });

    $btnDevSkip.addEventListener('click', function () {
        sessionStorage.setItem('verified', 'true');
        sessionStorage.setItem('verified_model', selection.model);
        sessionStorage.setItem('verified_year', selection.year);
        sessionStorage.setItem('verified_colour', selection.colour);
        $uploadCombo.textContent = comboLabel();
        setStep(3);
    });

    $btnSubmit.addEventListener('click', submitToGitHub);

    $btnUploadPrev.addEventListener('click', function () {
        goToLayer(currentLayerIndex - 1);
    });

    $btnUploadNext.addEventListener('click', function () {
        if (currentLayerIndex === LAYERS.length - 1) {
            // Last layer — scroll to submit button
            $btnSubmit.scrollIntoView({ behavior: 'smooth', block: 'center' });
        } else {
            goToLayer(currentLayerIndex + 1);
        }
    });

    document.addEventListener('keydown', function (e) {
        // Only navigate when step 3 is visible and no input is focused
        if ($step3.hidden) return;
        var tag = document.activeElement.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

        if (e.key === 'ArrowLeft') {
            e.preventDefault();
            goToLayer(currentLayerIndex - 1);
        } else if (e.key === 'ArrowRight') {
            e.preventDefault();
            if (currentLayerIndex === LAYERS.length - 1) {
                $btnSubmit.scrollIntoView({ behavior: 'smooth', block: 'center' });
            } else {
                goToLayer(currentLayerIndex + 1);
            }
        }
    });

    $btnBack1From3.addEventListener('click', function () {
        sessionStorage.removeItem('verified');
        sessionStorage.removeItem('verified_model');
        sessionStorage.removeItem('verified_year');
        sessionStorage.removeItem('verified_colour');
        resetUploadState();
        setStep(1);
    });

    // ── Init ───────────────────────────────────────────────────────────

    // Check for verification callback first (before status.json loads)
    var hasCallback = checkVerificationCallback();

    // Determine base URL for status.json — works on GitHub Pages and local dev
    var statusUrl = (function () {
        var base = window.location.origin + window.location.pathname;
        // If served from /docs/ subdir locally, go up one level
        if (base.indexOf('/docs/') !== -1) {
            return base.replace(/\/docs\/.*$/, '/status.json');
        }
        // GitHub Pages: status.json is at repo root, served alongside docs/
        // Fetch from the raw main branch as a reliable fallback
        return 'https://raw.githubusercontent.com/' + REPO + '/main/status.json';
    })();

    fetch(statusUrl)
        .then(function (resp) {
            if (!resp.ok) throw new Error('Failed to load status.json');
            return resp.json();
        })
        .then(function (data) {
            statusData = data;
            populateModels();

            // If we didn't arrive via a callback link, check sessionStorage
            if (!hasCallback) {
                checkSessionVerification();
            }
        })
        .catch(function (err) {
            $model.innerHTML = '<option value="">Failed to load — please refresh</option>';
            console.error('Error loading status.json:', err);
        });
})();
