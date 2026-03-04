// Tesla Card — Local Testbed upload logic
// Vanilla JS, no frameworks

(function () {
    'use strict';

    // ── Auto-crop ────────────────────────────────────────────────────
    // Crops phone screenshots to just the car rendering, removing the
    // cellular/battery/wifi status bar at top and app UI below the car.

    function cropScreenshot(file, callback) {
        var img = new Image();
        var url = URL.createObjectURL(file);
        img.onload = function () {
            var canvas = document.createElement('canvas');
            var ctx = canvas.getContext('2d');
            canvas.width = img.width;
            canvas.height = img.height;
            ctx.drawImage(img, 0, 0);

            var data = ctx.getImageData(0, 0, canvas.width, canvas.height);
            var px = data.data;
            var w = canvas.width;
            var h = canvas.height;

            // Sample background color from top-left 20x20 block
            var sampleN = Math.min(20, Math.floor(w * 0.02) || 1);
            var bgR = 0, bgG = 0, bgB = 0, cnt = 0;
            for (var sy = 0; sy < sampleN; sy++) {
                for (var sx = 0; sx < sampleN; sx++) {
                    var si = (sy * w + sx) * 4;
                    bgR += px[si]; bgG += px[si + 1]; bgB += px[si + 2];
                    cnt++;
                }
            }
            bgR /= cnt; bgG /= cnt; bgB /= cnt;

            // For each row, measure fraction of non-background pixels
            var threshold = 30; // Euclidean RGB distance
            var minCoverage = 0.03; // 3% of row width = car content
            var topY = 0, bottomY = h - 1;

            for (var y = 0; y < h; y++) {
                var nonBg = 0;
                for (var x = 0; x < w; x++) {
                    var i = (y * w + x) * 4;
                    var dr = px[i] - bgR, dg = px[i + 1] - bgG, db = px[i + 2] - bgB;
                    if (Math.sqrt(dr * dr + dg * dg + db * db) > threshold) nonBg++;
                }
                if (nonBg / w >= minCoverage) { topY = y; break; }
            }

            for (var y = h - 1; y >= 0; y--) {
                var nonBg = 0;
                for (var x = 0; x < w; x++) {
                    var i = (y * w + x) * 4;
                    var dr = px[i] - bgR, dg = px[i + 1] - bgG, db = px[i + 2] - bgB;
                    if (Math.sqrt(dr * dr + dg * dg + db * db) > threshold) nonBg++;
                }
                if (nonBg / w >= minCoverage) { bottomY = y; break; }
            }

            // Padding: 2% of car height
            var carH = bottomY - topY;
            var pad = Math.max(2, Math.floor(carH * 0.02));
            topY = Math.max(0, topY - pad);
            bottomY = Math.min(h - 1, bottomY + pad);

            var cropH = bottomY - topY + 1;
            var cropCanvas = document.createElement('canvas');
            cropCanvas.width = w;
            cropCanvas.height = cropH;
            cropCanvas.getContext('2d').drawImage(canvas, 0, topY, w, cropH, 0, 0, w, cropH);

            cropCanvas.toBlob(function (blob) {
                var cropped = new File([blob], file.name, { type: 'image/png' });
                URL.revokeObjectURL(url);
                callback(cropped, cropCanvas.toDataURL('image/png'));
            }, 'image/png');
        };
        img.src = url;
    }

    // ── Screenshot layers ─────────────────────────────────────────────
    // Must match filenames expected by local_testbed.py and process_screenshots.py

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
            'Wait for the frunk to reach its fully-open position',
            'Take a screenshot once the frunk is completely still'
          ] } },
        { key: 'trunk', filename: 'rt.png', label: 'Trunk Open',
          description: 'Side view with the rear trunk (boot) open.',
          instruction: { title: 'Trunk open', steps: [
            'Open the Tesla app \u2192 Controls',
            'Tap Trunk to open the rear trunk',
            'Wait for the trunk to reach its fully-open position',
            'Take a screenshot once the trunk is completely still'
          ] } },
        { key: 'front_doors', filename: 'front_doors.png', label: 'Both Front Doors',
          description: 'Side view with BOTH front doors open simultaneously.',
          instruction: { title: 'Both front doors open', steps: [
            'Open the Tesla app \u2192 Controls',
            'Open BOTH front doors simultaneously',
            'Wait for both door-open animations to finish completely',
            'Take a screenshot \u2014 both front doors should be fully open'
          ] } },
        { key: 'rear_doors', filename: 'rear_doors.png', label: 'Both Rear Doors',
          description: 'Side view with BOTH rear doors open simultaneously.',
          instruction: { title: 'Both rear doors open', steps: [
            'Open the Tesla app \u2192 Controls',
            'Close front doors, then open BOTH rear doors',
            'Wait for both door-open animations to finish completely',
            'Take a screenshot \u2014 both rear doors should be fully open'
          ] } },
        { key: 'all_doors', filename: 'all_doors.png', label: 'All 4 Doors',
          description: 'Side view with ALL four doors open simultaneously.',
          instruction: { title: 'All four doors open', steps: [
            'Open the Tesla app \u2192 Controls',
            'Open ALL four doors at the same time',
            'Wait for all door-open animations to finish completely',
            'Take a screenshot \u2014 all four doors should be fully open'
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
            'Turn OFF the climate system \u2014 seat heater icons must be hidden',
            'Wait for the UI to settle',
            'Take a screenshot of the full top-down interior view'
          ] } },

        // ── On charge ────────────────────────────────────────────────
        { key: 'oc_closed', filename: 'oc_closed.png', label: 'All Closed', section: 'On Charge',
          description: 'Side view while charging \u2014 cable plugged in, all doors closed.',
          instruction: { title: 'Charging base \u2014 all closed', steps: [
            'Plug in the charger and start a charging session',
            'Open the Tesla app \u2192 scroll down to Controls',
            'Ensure ALL doors, trunk and frunk are closed',
            'Wait for any animation to finish, then screenshot'
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
          description: 'Side view while charging with BOTH front doors open.',
          instruction: { title: 'Charging \u2014 both front doors', steps: [
            'Keep the charger plugged in',
            'Open BOTH front doors simultaneously',
            'Wait for both door-open animations to finish completely',
            'Take a screenshot \u2014 cable and both open doors should be visible'
          ] } },
        { key: 'oc_rear_doors', filename: 'oc_rear_doors.png', label: 'Both Rear Doors',
          description: 'Side view while charging with BOTH rear doors open.',
          instruction: { title: 'Charging \u2014 both rear doors', steps: [
            'Keep the charger plugged in',
            'Close front doors, then open BOTH rear doors',
            'Wait for both door-open animations to finish completely',
            'Take a screenshot \u2014 cable and both open doors should be visible'
          ] } },
        { key: 'oc_all_doors', filename: 'oc_all_doors.png', label: 'All 4 Doors',
          description: 'Side view while charging with ALL four doors open.',
          instruction: { title: 'Charging \u2014 all four doors', steps: [
            'Keep the charger plugged in',
            'Open ALL four doors at the same time',
            'Wait for all door-open animations to finish completely',
            'Take a screenshot \u2014 cable and all four open doors should be visible'
          ] } },
    ];

    // ── DOM refs ────────────────────────────────────────────────────────

    var $model = document.getElementById('select-model');
    var $variant = document.getElementById('select-variant');
    var $colour = document.getElementById('select-colour');
    var $colourStatus = document.getElementById('colour-status-msg');
    var $btnToUpload = document.getElementById('btn-to-upload');

    var $step1 = document.getElementById('step-1');
    var $step2 = document.getElementById('step-2');
    var $step3 = document.getElementById('step-3');

    var $uploadCombo = document.getElementById('upload-combo-label');
    var $uploadSidebar = document.getElementById('upload-sidebar-list');
    var $uploadGuide = document.getElementById('upload-guide');
    var $uploadInstructions = document.getElementById('upload-instructions');
    var $uploadDropzoneArea = document.getElementById('upload-dropzone-area');
    var $btnUploadPrev = document.getElementById('btn-upload-prev');
    var $btnUploadNext = document.getElementById('btn-upload-next');
    var $uploadNavIndicator = document.getElementById('upload-nav-indicator');
    var $uploadCounter = document.getElementById('upload-counter');
    var $btnSubmit = document.getElementById('btn-submit');
    var $uploadError = document.getElementById('upload-error');
    var $btnBack1 = document.getElementById('btn-back-to-step1');

    var $processDesc = document.getElementById('process-desc');
    var $processSpinner = document.getElementById('process-spinner');
    var $processStatusText = document.getElementById('process-status-text');
    var $progressBarWrap = document.getElementById('progress-bar-wrap');
    var $progressBar = document.getElementById('progress-bar');
    var $logArea = document.getElementById('log-area');
    var $processResult = document.getElementById('process-result');
    var $resultOffcharge = document.getElementById('result-offcharge');
    var $resultOncharge = document.getElementById('result-oncharge');
    var $resultOffchargePath = document.getElementById('result-offcharge-path');
    var $resultOnchargePath = document.getElementById('result-oncharge-path');
    var $btnRetry = document.getElementById('btn-retry');
    var $btnStartOver = document.getElementById('btn-start-over');
    var $container = document.getElementById('app-container');
    var $actionButtons = document.getElementById('action-buttons');
    var $btnPreview = document.getElementById('btn-preview');
    var $btnPromote = document.getElementById('btn-promote');
    var $promoteMsg = document.getElementById('promote-msg');
    var $colourResetWrap = document.getElementById('colour-reset-wrap');
    var $btnResetStatus = document.getElementById('btn-reset-status');

    // Branch review DOM
    var $branchReviewSection = document.getElementById('branch-review-section');
    var $selectBranch = document.getElementById('select-branch');
    var $btnReviewPreview = document.getElementById('btn-review-preview');
    var $btnReviewPromote = document.getElementById('btn-review-promote');
    var $btnReviewDelete = document.getElementById('btn-review-delete');
    var $reviewPromoteMsg = document.getElementById('review-promote-msg');

    // Preview modal DOM
    var $previewModal = document.getElementById('preview-modal');
    var $previewClose = document.getElementById('preview-close');
    var $previewTitle = document.getElementById('preview-title');
    var $previewModeToggle = document.getElementById('preview-mode-toggle');
    var $previewCar = document.getElementById('preview-car');
    var $previewControls = document.getElementById('preview-controls');

    var stepperSteps = document.querySelectorAll('.stepper-step');

    // ── State ───────────────────────────────────────────────────────────

    var modelsData = null;   // from card repo models.json
    var statusData = null;   // flat status map {path: {status, pr?}}
    var selection = { model: '', variant: '', colour: '' };
    var uploadedFiles = {};
    var currentLayerIndex = 0;
    var layerDOMCache = {};
    var lastSubmissionDir = '';
    var reviewRef = null;  // set when previewing a git branch

    // Preview state
    var previewMode = 'offcharge';  // 'offcharge' or 'oncharge'
    var previewToggles = {
        chargeport: false,
        frunk: false,
        trunk: false,
        nf: false,
        nr: false,
        ff: false,
        fr: false,
    };

    // Z-order constants (DOM order = paint order, later = on top)
    var Z_ORDER_OFFCHARGE = ['chargeport', 'frunk', 'fr', 'ff', 'nr', 'nf'];
    var Z_ORDER_ONCHARGE = ['fr', 'ff', 'frunk', 'nf', 'nr'];

    // Toggle definitions per mode
    var TOGGLE_DEFS = {
        offcharge: [
            { key: 'chargeport', label: 'Chargeport' },
            { key: 'frunk', label: 'Frunk' },
            { key: 'trunk', label: 'Trunk' },
            { key: 'nf', label: 'Near-front' },
            { key: 'nr', label: 'Near-rear' },
            { key: 'ff', label: 'Far-front' },
            { key: 'fr', label: 'Far-rear' },
        ],
        oncharge: [
            { key: 'frunk', label: 'Frunk' },
            { key: 'trunk', label: 'Trunk' },
            { key: 'nf', label: 'Near-front' },
            { key: 'nr', label: 'Near-rear' },
            { key: 'ff', label: 'Far-front' },
            { key: 'fr', label: 'Far-rear' },
        ],
    };

    // ── Helpers ─────────────────────────────────────────────────────────

    function getStatus(modelId, variantId, colourId) {
        var key = modelId + '/' + variantId + '/' + colourId;
        return statusData[key] || { status: 'available' };
    }

    function findModel(id) {
        if (!modelsData) return null;
        for (var i = 0; i < modelsData.models.length; i++) {
            if (modelsData.models[i].id === id) return modelsData.models[i];
        }
        return null;
    }

    function findVariant(modelId, variantId) {
        var m = findModel(modelId);
        if (!m) return null;
        for (var i = 0; i < m.variants.length; i++) {
            if (m.variants[i].id === variantId) return m.variants[i];
        }
        return null;
    }

    function findColour(modelId, variantId, colourId) {
        var v = findVariant(modelId, variantId);
        if (!v) return null;
        for (var i = 0; i < v.colours.length; i++) {
            if (v.colours[i].id === colourId) return v.colours[i];
        }
        return null;
    }

    function comboLabel() {
        if (!modelsData || !selection.model || !selection.variant || !selection.colour) return '';
        var m = findModel(selection.model);
        var v = findVariant(selection.model, selection.variant);
        var c = findColour(selection.model, selection.variant, selection.colour);
        if (!m || !v || !c) return '';
        return m.name + ' ' + v.label + ' \u2014 ' + c.name;
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
        // Widen container for upload step
        $container.classList.toggle('container-wide', n === 2);

        // Stagger animate the visible step's children
        var activeStep = document.getElementById('step-' + n);
        if (activeStep) {
            activeStep.classList.remove('stagger-children');
            void activeStep.offsetWidth; // force reflow
            activeStep.classList.add('stagger-children');
        }

        window.scrollTo(0, 0);
    }

    // All-doors screenshots are optional — improve combined overlays but not required
    var OPTIONAL_KEYS = { all_doors: true, oc_all_doors: true };

    function requiredCount() {
        return LAYERS.filter(function (l) { return !OPTIONAL_KEYS[l.key]; }).length;
    }

    function uploadCount() {
        return Object.keys(uploadedFiles).length;
    }

    function requiredUploadCount() {
        var n = 0;
        Object.keys(uploadedFiles).forEach(function (k) {
            if (!OPTIONAL_KEYS[k]) n++;
        });
        return n;
    }

    function updateCounter() {
        $uploadCounter.textContent = uploadCount() + ' of ' + LAYERS.length + ' screenshots uploaded';
        $btnSubmit.disabled = requiredUploadCount() < requiredCount();
    }

    // ── Step 1: Model / variant / colour selection ──────────────────────

    function loadStatus() {
        Promise.all([
            fetch('/api/models').then(function (r) { return r.json(); }),
            fetch('/api/status').then(function (r) { return r.json(); })
        ])
        .then(function (results) {
            modelsData = results[0];
            statusData = results[1];
            populateModels();
        })
        .catch(function (err) {
            $model.innerHTML = '<option value="">Failed to load</option>';
            console.error('Failed to load data:', err);
        });
    }

    function populateModels() {
        $model.innerHTML = '<option value="">Choose a model...</option>';
        modelsData.models.forEach(function (m) {
            var opt = document.createElement('option');
            opt.value = m.id;
            opt.textContent = m.name;
            $model.appendChild(opt);
        });
        $model.disabled = false;
    }

    $model.addEventListener('change', function () {
        selection.model = $model.value;
        selection.variant = '';
        selection.colour = '';
        $variant.innerHTML = '<option value="">Choose a variant...</option>';
        $colour.innerHTML = '<option value="">Select a variant first</option>';
        $colour.disabled = true;
        $btnToUpload.disabled = true;
        $colourStatus.hidden = true;

        if (!selection.model) {
            $variant.disabled = true;
            return;
        }

        var m = findModel(selection.model);
        if (!m) return;
        m.variants.forEach(function (v) {
            var opt = document.createElement('option');
            opt.value = v.id;
            opt.textContent = v.label;
            $variant.appendChild(opt);
        });
        $variant.disabled = false;
    });

    $variant.addEventListener('change', function () {
        selection.variant = $variant.value;
        selection.colour = '';
        $colour.innerHTML = '<option value="">Choose a colour...</option>';
        $btnToUpload.disabled = true;
        $colourStatus.hidden = true;

        if (!selection.variant) {
            $colour.disabled = true;
            return;
        }

        var v = findVariant(selection.model, selection.variant);
        if (!v) return;
        v.colours.forEach(function (c) {
            var opt = document.createElement('option');
            opt.value = c.id;
            opt.textContent = c.name;
            var entry = getStatus(selection.model, selection.variant, c.id);
            if (entry.status === 'pending') {
                opt.textContent += ' \u2014 under review';
                opt.disabled = true;
            } else if (entry.status === 'complete') {
                opt.textContent += ' \u2014 complete';
            } else if (branchForCombo(selection.model, selection.variant, c.id)) {
                opt.textContent += ' \u2014 pending approval';
                opt.disabled = true;
            }
            $colour.appendChild(opt);
        });
        $colour.disabled = false;
    });

    $colour.addEventListener('change', function () {
        selection.colour = $colour.value;
        $colourStatus.hidden = true;
        $colourResetWrap.hidden = true;
        $btnToUpload.disabled = !selection.colour;

        if (selection.colour) {
            var entry = getStatus(selection.model, selection.variant, selection.colour);
            var pendingBranch = branchForCombo(selection.model, selection.variant, selection.colour);
            if (entry.status === 'pending') {
                $colourStatus.textContent = 'This combination is currently under review' + (entry.pr ? ' (PR #' + entry.pr + ')' : '') + '.';
                $colourStatus.className = 'status-msg pending';
                $colourStatus.hidden = false;
                $btnToUpload.disabled = true;
            } else if (entry.status === 'complete') {
                $colourStatus.textContent = 'This combination is already available in the card.';
                $colourStatus.className = 'status-msg complete';
                $colourStatus.hidden = false;
                $colourResetWrap.hidden = false;
                $btnToUpload.disabled = true;
            } else if (pendingBranch) {
                $colourStatus.textContent = 'This combination has a submission branch pending approval.';
                $colourStatus.className = 'status-msg pending';
                $colourStatus.hidden = false;
                $btnToUpload.disabled = true;
            }
        }
    });

    $btnResetStatus.addEventListener('click', function () {
        if (!selection.model || !selection.variant || !selection.colour) return;

        $btnResetStatus.disabled = true;
        $btnResetStatus.textContent = 'Clearing...';

        // Delete any existing branch + dir, then reset status
        var branchName = 'submissions/' + selection.model + '-' +
            selection.variant + '-' + selection.colour;

        fetch('/api/branch/' + encodeURIComponent(branchName), { method: 'DELETE' })
            .then(function () { /* ignore branch-not-found */ })
            .then(function () {
                return fetch('/api/reset-status', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        model: selection.model,
                        variant: selection.variant,
                        colour: selection.colour,
                    }),
                });
            })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                $btnResetStatus.disabled = false;
                $btnResetStatus.textContent = 'Clear (dev)';
                if (data.success) {
                    var savedColour = selection.colour;
                    fetch('/api/status')
                        .then(function (r) { return r.json(); })
                        .then(function (d) {
                            statusData = d;
                            // Rebuild colour list, then re-select the cleared colour
                            $variant.dispatchEvent(new Event('change'));
                            $colour.value = savedColour;
                            selection.colour = savedColour;
                            $colourStatus.hidden = true;
                            $colourResetWrap.hidden = true;
                            $btnToUpload.disabled = false;
                        });
                }
            })
            .catch(function () {
                $btnResetStatus.disabled = false;
                $btnResetStatus.textContent = 'Clear (dev)';
            });
    });

    $btnToUpload.addEventListener('click', function () {
        $uploadCombo.textContent = comboLabel();
        initUploadWizard();
        setStep(2);
    });

    // ── Step 2: Upload wizard ───────────────────────────────────────────

    function initUploadWizard() {
        uploadedFiles = {};
        currentLayerIndex = 0;
        layerDOMCache = {};
        buildSidebar();
        showLayer(0);
        updateCounter();
        checkDevFiles();
    }

    function buildSidebar() {
        $uploadSidebar.innerHTML = '';
        LAYERS.forEach(function (layer, i) {
            if (layer.section) {
                var header = document.createElement('li');
                header.className = 'upload-sidebar-header';
                header.textContent = layer.section;
                $uploadSidebar.appendChild(header);
            }
            var li = document.createElement('li');
            li.className = 'upload-sidebar-item' + (i === 0 ? ' active' : '');
            li.setAttribute('data-index', i);
            li.innerHTML =
                '<span class="upload-sidebar-num">' + (i + 1) + '</span>' +
                '<span class="upload-sidebar-label">' + layer.label + '</span>';
            li.addEventListener('click', function () { showLayer(i); });
            $uploadSidebar.appendChild(li);
        });
    }

    function showLayer(index) {
        currentLayerIndex = index;
        var layer = LAYERS[index];

        // Update sidebar active state
        var items = $uploadSidebar.querySelectorAll('.upload-sidebar-item');
        items.forEach(function (el) {
            var idx = parseInt(el.getAttribute('data-index'), 10);
            el.classList.toggle('active', idx === index);
            el.classList.toggle('done', !!uploadedFiles[LAYERS[idx].key]);
        });

        // Guide image inside iPhone mockup
        var guidePath = 'assets/guides/' + layer.key + '.png';
        $uploadGuide.innerHTML =
            '<div class="phone-mockup phone-animate-in">' +
            '<div class="phone-notch"></div>' +
            '<div class="phone-screen">' +
            '<img src="' + guidePath + '" alt="Guide: ' + layer.label + '"' +
            ' onerror="this.closest(\'.phone-mockup\').outerHTML=\'<div class=upload-guide-fallback>' +
            layer.description + '</div>\'">' +
            '</div>' +
            '<div class="phone-home-bar"></div>' +
            '</div>';

        // Instructions
        var html = '<div class="upload-instructions-title">' + layer.instruction.title + '</div>' +
                   '<ol class="upload-instructions-steps">';
        layer.instruction.steps.forEach(function (s) {
            html += '<li>' + s + '</li>';
        });
        html += '</ol>';
        $uploadInstructions.innerHTML = html;

        // Dropzone
        renderDropzone(layer);

        // Nav
        $uploadNavIndicator.textContent = (index + 1) + ' / ' + LAYERS.length;
        $btnUploadPrev.disabled = index === 0;
        $btnUploadNext.disabled = index === LAYERS.length - 1;

        // Stagger animate wizard content
        var uploadMain = $uploadGuide.parentNode;
        if (uploadMain) {
            uploadMain.classList.remove('stagger-content');
            void uploadMain.offsetWidth;
            uploadMain.classList.add('stagger-content');
        }
    }

    function renderDropzone(layer) {
        var existing = uploadedFiles[layer.key];
        if (existing) {
            $uploadDropzoneArea.innerHTML =
                '<div class="upload-dropzone filled">' +
                '<div class="upload-thumb-wrap">' +
                '<img class="upload-thumb" src="' + existing.dataUrl + '" alt="' + layer.label + '">' +
                '<button class="upload-remove" title="Remove" type="button">&times;</button>' +
                '</div></div>';
            $uploadDropzoneArea.querySelector('.upload-remove').addEventListener('click', function () {
                delete uploadedFiles[layer.key];
                renderDropzone(layer);
                updateCounter();
                updateSidebarDone();
            });
        } else {
            $uploadDropzoneArea.innerHTML =
                '<div class="upload-dropzone" id="dropzone-active">' +
                '<div class="upload-dropzone-empty">' +
                '<div class="upload-dropzone-icon">\u2191</div>' +
                '<div class="upload-dropzone-text">Click or drag PNG here</div>' +
                '</div></div>' +
                '<input type="file" id="file-input-hidden" accept="image/png" style="display:none">';

            var dz = document.getElementById('dropzone-active');
            var fi = document.getElementById('file-input-hidden');

            dz.addEventListener('click', function () { fi.click(); });
            dz.addEventListener('dragover', function (e) {
                e.preventDefault();
                dz.classList.add('dragover');
            });
            dz.addEventListener('dragleave', function () {
                dz.classList.remove('dragover');
            });
            dz.addEventListener('drop', function (e) {
                e.preventDefault();
                dz.classList.remove('dragover');
                if (e.dataTransfer.files.length > 0) {
                    handleFile(layer, e.dataTransfer.files[0]);
                }
            });
            fi.addEventListener('change', function () {
                if (fi.files.length > 0) {
                    handleFile(layer, fi.files[0]);
                }
            });
        }
    }

    function handleFile(layer, file) {
        if (!file.type.match('image/png') && !file.name.toLowerCase().endsWith('.png')) {
            $uploadError.textContent = 'Only PNG files are accepted.';
            $uploadError.hidden = false;
            return;
        }
        $uploadError.hidden = true;

        // Auto-crop: remove phone status bar + app UI below the car
        cropScreenshot(file, function (croppedFile, dataUrl) {
            uploadedFiles[layer.key] = {
                file: croppedFile,
                dataUrl: dataUrl,
            };
            renderDropzone(layer);
            updateCounter();
            updateSidebarDone();

            // Auto-advance to next layer
            if (currentLayerIndex < LAYERS.length - 1) {
                setTimeout(function () { showLayer(currentLayerIndex + 1); }, 300);
            }
        });
    }

    function updateSidebarDone() {
        var items = $uploadSidebar.querySelectorAll('.upload-sidebar-item');
        items.forEach(function (el) {
            var idx = parseInt(el.getAttribute('data-index'), 10);
            el.classList.toggle('done', !!uploadedFiles[LAYERS[idx].key]);
        });
    }

    // ── Auto-populate from dev directory ────────────────────────────────

    function checkDevFiles() {
        fetch('/api/dev-files')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.available && Object.keys(data.available).length > 0) {
                    showAutoPopulateButton(data.available);
                }
            })
            .catch(function () { /* no dev-dir configured, skip */ });
    }

    function showAutoPopulateButton(available) {
        var existing = document.getElementById('btn-auto-populate');
        if (existing) return;

        var btn = document.createElement('button');
        btn.id = 'btn-auto-populate';
        btn.className = 'btn btn-secondary';
        btn.style.marginLeft = '8px';
        btn.textContent = 'Auto-fill from dev dir (' + Object.keys(available).length + '/' + LAYERS.length + ')';
        $btnSubmit.parentNode.insertBefore(btn, $btnSubmit);

        btn.addEventListener('click', function () {
            btn.disabled = true;
            btn.textContent = 'Loading...';
            autoPopulate(available, function () {
                btn.textContent = 'Loaded!';
            });
        });
    }

    function autoPopulate(available, done) {
        var keys = Object.keys(available);
        var loaded = 0;

        keys.forEach(function (key) {
            fetch('/api/dev-file/' + key)
                .then(function (r) { return r.blob(); })
                .then(function (blob) {
                    var file = new File([blob], key + '.png', { type: 'image/png' });
                    var layer = LAYERS.find(function (l) { return l.key === key; });
                    if (!layer) { finish(); return; }

                    cropScreenshot(file, function (croppedFile, dataUrl) {
                        uploadedFiles[key] = { file: croppedFile, dataUrl: dataUrl };
                        finish();
                    });
                })
                .catch(function () { finish(); });
        });

        function finish() {
            loaded++;
            if (loaded >= keys.length) {
                updateCounter();
                updateSidebarDone();
                showLayer(currentLayerIndex);
                if (done) done();
            }
        }
    }

    $btnUploadPrev.addEventListener('click', function () {
        if (currentLayerIndex > 0) showLayer(currentLayerIndex - 1);
    });

    $btnUploadNext.addEventListener('click', function () {
        if (currentLayerIndex < LAYERS.length - 1) showLayer(currentLayerIndex + 1);
    });

    $btnBack1.addEventListener('click', function () { setStep(1); });

    // ── Step 2 -> 3: Submit ─────────────────────────────────────────────

    $btnSubmit.addEventListener('click', function () {
        $btnSubmit.disabled = true;
        setStep(3);
        submitFiles();
    });

    function setProgress(message, pct) {
        $processStatusText.textContent = message;
        $progressBarWrap.hidden = false;
        $progressBar.style.width = pct + '%';
    }

    function submitFiles() {
        $logArea.textContent = '';
        $processResult.hidden = true;
        $btnRetry.hidden = true;
        $processSpinner.style.display = '';
        $progressBarWrap.hidden = false;
        $progressBar.style.width = '0%';
        setProgress('Uploading files...', 2);
        $processDesc.textContent = 'Processing ' + comboLabel() + '...';

        var formData = new FormData();
        formData.append('model', selection.model);
        formData.append('variant', selection.variant);
        formData.append('colour', selection.colour);

        LAYERS.forEach(function (layer) {
            var entry = uploadedFiles[layer.key];
            if (entry) {
                formData.append(layer.key, entry.file, layer.filename);
            }
        });

        fetch('/api/submit', { method: 'POST', body: formData })
            .then(function (response) {
                // 409 = branch exists, returned as plain JSON (not streamed)
                if (response.status === 409) {
                    return response.json().then(function (d) {
                        appendLog('Branch exists, removing and resubmitting...', 'heading');
                        var branchName = 'submissions/' + selection.model + '-' +
                            selection.variant + '-' + selection.colour;
                        return fetch('/api/branch/' + encodeURIComponent(branchName), { method: 'DELETE' })
                            .then(function (r) { return r.json(); })
                            .then(function (dd) {
                                if (dd.success) { submitFiles(); }
                                else {
                                    appendLog(dd.error || 'Failed to remove branch', 'err');
                                    $processSpinner.style.display = 'none';
                                    $progressBarWrap.hidden = true;
                                    $processStatusText.textContent = 'Failed';
                                }
                            });
                    });
                }

                // Non-200 plain JSON errors
                if (response.status >= 400) {
                    return response.json().then(function (d) {
                        appendLog(d.error || 'Submission failed', 'err');
                        $processSpinner.style.display = 'none';
                        $progressBarWrap.hidden = true;
                        $processStatusText.textContent = 'Failed';
                    });
                }

                // Stream NDJSON progress events
                var reader = response.body.getReader();
                var decoder = new TextDecoder();
                var buffer = '';

                function processChunk(result) {
                    if (result.done) {
                        finishSubmit();
                        return;
                    }
                    buffer += decoder.decode(result.value, { stream: true });
                    var lines = buffer.split('\n');
                    buffer = lines.pop(); // keep incomplete line in buffer

                    lines.forEach(function (line) {
                        if (!line.trim()) return;
                        try {
                            var event = JSON.parse(line);
                            handleStreamEvent(event);
                        } catch (e) { /* skip malformed lines */ }
                    });

                    return reader.read().then(processChunk);
                }

                return reader.read().then(processChunk);
            })
            .catch(function (err) {
                appendLog('Network error: ' + err.message, 'err');
                $processSpinner.style.display = 'none';
                $progressBarWrap.hidden = true;
                $processStatusText.textContent = 'Failed';
            });
    }

    var lastResult = null;

    function handleStreamEvent(event) {
        if (event.type === 'progress') {
            setProgress(event.message, event.pct);
        } else if (event.type === 'log') {
            var text = event.text;
            if (text.indexOf('===') === 0) {
                appendLog(text, 'heading');
            } else if (text.indexOf('FAILED') >= 0 || text.indexOf('ERROR') >= 0 ||
                        text.indexOf('Traceback') >= 0) {
                appendLog(text, 'err');
            } else {
                appendLog(text, 'ok');
            }
        } else if (event.type === 'result') {
            lastResult = event;
        }
    }

    function finishSubmit() {
        $processSpinner.style.display = 'none';

        if (!lastResult) {
            $processStatusText.textContent = 'Failed — no result received';
            $progressBarWrap.hidden = true;
            return;
        }

        var data = lastResult;

        if (data.error) {
            // Check if branch conflict
            if (data.error.indexOf('already exists') >= 0) {
                appendLog('Branch exists, removing and resubmitting...', 'heading');
                var branchName = 'submissions/' + selection.model + '-' +
                    selection.variant + '-' + selection.colour;
                fetch('/api/branch/' + encodeURIComponent(branchName), { method: 'DELETE' })
                    .then(function (r) { return r.json(); })
                    .then(function (d) {
                        if (d.success) { lastResult = null; submitFiles(); }
                        else { appendLog(d.error || 'Failed to remove branch', 'err'); }
                    })
                    .catch(function (err) {
                        appendLog('Failed: ' + err.message, 'err');
                    });
                return;
            }
            appendLog(data.error, 'err');
            $processStatusText.textContent = 'Failed';
            $progressBarWrap.hidden = true;
            return;
        }

        // Show accumulated log
        if (data.log) {
            data.log.forEach(function (line) {
                if (line.indexOf('===') === 0) {
                    appendLog(line, 'heading');
                } else if (line.indexOf('FAILED') >= 0 || line.indexOf('ERROR') >= 0) {
                    appendLog(line, 'err');
                } else {
                    appendLog(line, 'ok');
                }
            });
        }

        setProgress(data.success ? 'Processing complete!' : 'Completed with errors', 100);

        if (data.success) {
            $processResult.hidden = false;
            if (data.offcharge_output) {
                $resultOffcharge.hidden = false;
                $resultOffchargePath.textContent = data.offcharge_output;
            }
            if (data.oncharge_output) {
                $resultOncharge.hidden = false;
                $resultOnchargePath.textContent = data.oncharge_output;
            }
            appendLog('\nBranch: ' + data.branch, 'heading');
            appendLog('Submission dir: ' + data.submission_dir, 'ok');

            // Store submission dir and show action buttons
            lastSubmissionDir = 'submissions/' + selection.model + '-' +
                selection.variant + '-' + selection.colour;
            $actionButtons.hidden = false;
            $actionButtons.style.display = 'flex';
            $btnPromote.disabled = false;
            $btnPromote.textContent = 'Promote to card repo';
            $promoteMsg.hidden = true;
        }

        $btnRetry.hidden = false;
        lastResult = null;
    }

    function appendLog(text, type) {
        if (!text) return;
        var span = document.createElement('span');
        if (type === 'ok') span.className = 'log-ok';
        else if (type === 'err') span.className = 'log-err';
        else if (type === 'heading') span.className = 'log-heading';
        span.textContent = text + '\n';
        $logArea.appendChild(span);
        $logArea.scrollTop = $logArea.scrollHeight;
    }

    $btnRetry.addEventListener('click', function () {
        var branchName = 'submissions/' + selection.model + '-' +
            selection.variant + '-' + selection.colour;
        $btnRetry.disabled = true;
        $btnRetry.textContent = 'Deleting branch...';
        fetch('/api/branch/' + encodeURIComponent(branchName), { method: 'DELETE' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                $btnRetry.disabled = false;
                $btnRetry.textContent = 'Delete branch & retry';
                if (data.success) {
                    appendLog('Branch removed. Resubmitting...', 'heading');
                    submitFiles();
                } else {
                    appendLog(data.error || 'Failed to remove branch', 'err');
                }
            })
            .catch(function (err) {
                $btnRetry.disabled = false;
                $btnRetry.textContent = 'Delete branch & retry';
                appendLog('Failed: ' + err.message, 'err');
            });
    });

    $btnStartOver.addEventListener('click', function () {
        uploadedFiles = {};
        selection = { model: '', variant: '', colour: '' };
        $model.value = '';
        $variant.innerHTML = '<option value="">Select a model first</option>';
        $variant.disabled = true;
        $colour.innerHTML = '<option value="">Select a variant first</option>';
        $colour.disabled = true;
        $btnToUpload.disabled = true;
        $colourStatus.hidden = true;
        loadBranches();
        setStep(1);
    });

    // ── Preview modal ──────────────────────────────────────────────────

    function openPreview() {
        previewMode = 'offcharge';
        // Reset all toggles
        Object.keys(previewToggles).forEach(function (k) { previewToggles[k] = false; });

        $previewTitle.textContent = 'Preview — ' + comboLabel();
        $previewModal.hidden = false;
        document.body.style.overflow = 'hidden';

        // Set mode toggle active state
        updateModeToggle();
        buildPreviewControls();
        renderPreviewCar();
    }

    function closePreview() {
        $previewModal.hidden = true;
        document.body.style.overflow = '';
    }

    $previewClose.addEventListener('click', closePreview);
    $previewModal.addEventListener('click', function (e) {
        if (e.target === $previewModal) closePreview();
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !$previewModal.hidden) closePreview();
    });

    // Mode toggle
    $previewModeToggle.addEventListener('click', function (e) {
        var btn = e.target.closest('.preview-mode-btn');
        if (!btn) return;
        var mode = btn.getAttribute('data-mode');
        if (mode === previewMode) return;

        previewMode = mode;
        // Reset toggles that don't exist in new mode
        var defs = TOGGLE_DEFS[mode];
        var validKeys = {};
        defs.forEach(function (d) { validKeys[d.key] = true; });
        Object.keys(previewToggles).forEach(function (k) {
            if (!validKeys[k]) previewToggles[k] = false;
        });

        updateModeToggle();
        buildPreviewControls();
        renderPreviewCar();
    });

    function updateModeToggle() {
        var btns = $previewModeToggle.querySelectorAll('.preview-mode-btn');
        btns.forEach(function (btn) {
            btn.classList.toggle('active', btn.getAttribute('data-mode') === previewMode);
        });
    }

    function buildPreviewControls() {
        var defs = TOGGLE_DEFS[previewMode];
        $previewControls.innerHTML = '';
        defs.forEach(function (def) {
            var btn = document.createElement('button');
            btn.className = 'preview-toggle' + (previewToggles[def.key] ? ' active' : '');
            btn.textContent = def.label;
            btn.setAttribute('data-key', def.key);
            btn.addEventListener('click', function () {
                previewToggles[def.key] = !previewToggles[def.key];
                btn.classList.toggle('active', previewToggles[def.key]);
                renderPreviewCar();
            });
            $previewControls.appendChild(btn);
        });
    }

    function previewImageUrl(filename) {
        var url = '/' + lastSubmissionDir + '/processed/' +
            (previewMode === 'oncharge' ? 'oncharge' : 'offcharge') +
            '/overlays/' + filename;
        if (reviewRef) {
            url += '?ref=' + encodeURIComponent(reviewRef);
        }
        return url;
    }

    function renderPreviewCar() {
        var prefix = previewMode === 'oncharge' ? 'oncharge-' : '';
        var zOrder = previewMode === 'oncharge' ? Z_ORDER_ONCHARGE : Z_ORDER_OFFCHARGE;

        // Base image — trunk-open swaps the entire base
        var baseFile = previewToggles.trunk ? prefix + 'trunk-open.png' : prefix + 'base.png';
        var html = '<img src="' + previewImageUrl(baseFile) + '" alt="base">';

        // Walk z-order and add active overlays
        zOrder.forEach(function (key) {
            if (!previewToggles[key]) return;

            // Check for combined same-side doors
            var overlayFile;
            if (key === 'nf' && previewToggles.nr) {
                overlayFile = prefix + 'nf-nr-combined-overlay.png';
            } else if (key === 'nr' && previewToggles.nf) {
                // Already handled by nf combined — skip individual nr
                return;
            } else if (key === 'ff' && previewToggles.fr) {
                overlayFile = prefix + 'ff-fr-combined-overlay.png';
            } else if (key === 'fr' && previewToggles.ff) {
                // Already handled by ff combined — skip individual fr
                return;
            } else {
                overlayFile = prefix + key + '-overlay.png';
            }

            html += '<img src="' + previewImageUrl(overlayFile) + '" alt="' + key + '">';
        });

        $previewCar.innerHTML = html;
    }

    $btnPreview.addEventListener('click', function () {
        reviewRef = null;  // normal flow — read from filesystem
        openPreview();
    });

    // ── Branch review ──────────────────────────────────────────────────

    var branchList = [];

    function branchForCombo(model, variant, colour) {
        for (var i = 0; i < branchList.length; i++) {
            if (branchList[i].model === model &&
                branchList[i].variant === variant &&
                branchList[i].colour === colour) {
                return branchList[i];
            }
        }
        return null;
    }

    function loadBranches() {
        return fetch('/api/branches')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                branchList = data.branches || [];
                if (branchList.length === 0) {
                    $branchReviewSection.hidden = true;
                } else {
                    $branchReviewSection.hidden = false;
                    $selectBranch.innerHTML = '<option value="">Choose a branch...</option>';
                    branchList.forEach(function (b) {
                        var opt = document.createElement('option');
                        opt.value = b.name;
                        opt.textContent = b.label;
                        $selectBranch.appendChild(opt);
                    });
                }
                // Re-trigger colour dropdown so "pending approval" locks refresh
                if (selection.variant) {
                    $variant.dispatchEvent(new Event('change'));
                }
            })
            .catch(function () {
                $branchReviewSection.hidden = true;
            });
    }

    $selectBranch.addEventListener('change', function () {
        var selected = $selectBranch.value;
        $btnReviewPreview.disabled = !selected;
        $btnReviewPromote.disabled = !selected;
        $btnReviewDelete.disabled = !selected;
        $reviewPromoteMsg.hidden = true;
    });

    $btnReviewPreview.addEventListener('click', function () {
        var selected = $selectBranch.value;
        if (!selected) return;

        // Find branch metadata
        var branch = null;
        for (var i = 0; i < branchList.length; i++) {
            if (branchList[i].name === selected) { branch = branchList[i]; break; }
        }
        if (!branch) return;

        // Set state for preview
        selection.model = branch.model;
        selection.variant = branch.variant;
        selection.colour = branch.colour;
        lastSubmissionDir = 'submissions/' + branch.model + '-' + branch.variant + '-' + branch.colour;
        // Only set reviewRef for git branches, not local dirs
        reviewRef = branch.source === 'local' ? null : branch.source;

        openPreview();
    });

    $btnReviewPromote.addEventListener('click', function () {
        var selected = $selectBranch.value;
        if (!selected) return;

        var branch = null;
        for (var i = 0; i < branchList.length; i++) {
            if (branchList[i].name === selected) { branch = branchList[i]; break; }
        }
        if (!branch) return;

        $btnReviewPromote.disabled = true;
        $btnReviewPromote.textContent = 'Promoting...';
        $reviewPromoteMsg.hidden = true;

        var promoteBody = {
            model: branch.model,
            variant: branch.variant,
            colour: branch.colour,
        };
        if (branch.source !== 'local') {
            promoteBody.ref = branch.source;
        }

        fetch('/api/promote', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(promoteBody),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.success) {
                $btnReviewPromote.textContent = 'Promoted!';
                $reviewPromoteMsg.className = 'status-msg complete';
                $reviewPromoteMsg.textContent = 'Copied ' + data.file_count +
                    ' files to ' + data.target_dir;
                $reviewPromoteMsg.hidden = false;
            } else {
                $btnReviewPromote.disabled = false;
                $btnReviewPromote.textContent = 'Promote to card repo';
                $reviewPromoteMsg.className = 'status-msg pending';
                $reviewPromoteMsg.textContent = data.error || 'Promote failed';
                $reviewPromoteMsg.hidden = false;
            }
        })
        .catch(function (err) {
            $btnReviewPromote.disabled = false;
            $btnReviewPromote.textContent = 'Promote to card repo';
            $reviewPromoteMsg.className = 'status-msg pending';
            $reviewPromoteMsg.textContent = 'Error: ' + err.message;
            $reviewPromoteMsg.hidden = false;
        });
    });

    $btnReviewDelete.addEventListener('click', function () {
        var selected = $selectBranch.value;
        if (!selected) return;
        if (!confirm('Delete branch "' + selected + '"? This cannot be undone.')) return;

        $btnReviewDelete.disabled = true;
        $btnReviewDelete.textContent = 'Deleting...';
        $reviewPromoteMsg.hidden = true;

        fetch('/api/branch/' + encodeURIComponent(selected), { method: 'DELETE' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                $btnReviewDelete.textContent = 'Delete branch';
                if (data.success || !data.error) {
                    $reviewPromoteMsg.className = 'status-msg complete';
                    $reviewPromoteMsg.textContent = 'Branch deleted.';
                    $reviewPromoteMsg.hidden = false;
                    // Refresh branch list (also re-triggers colour dropdown)
                    loadBranches();
                } else {
                    $btnReviewDelete.disabled = false;
                    $reviewPromoteMsg.className = 'status-msg pending';
                    $reviewPromoteMsg.textContent = data.error || 'Delete failed';
                    $reviewPromoteMsg.hidden = false;
                }
            })
            .catch(function (err) {
                $btnReviewDelete.disabled = false;
                $btnReviewDelete.textContent = 'Delete branch';
                $reviewPromoteMsg.className = 'status-msg pending';
                $reviewPromoteMsg.textContent = 'Error: ' + err.message;
                $reviewPromoteMsg.hidden = false;
            });
    });

    // ── Promote handler (post-processing flow) ──────────────────────────

    $btnPromote.addEventListener('click', function () {
        $btnPromote.disabled = true;
        $btnPromote.textContent = 'Promoting...';
        $promoteMsg.hidden = true;

        fetch('/api/promote', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: selection.model,
                variant: selection.variant,
                colour: selection.colour,
            }),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.success) {
                $btnPromote.textContent = 'Promoted!';
                $promoteMsg.textContent = 'Copied ' + data.file_count +
                    ' files to ' + data.target_dir;
                $promoteMsg.hidden = false;
                appendLog('Promoted ' + data.file_count + ' files to ' + data.target_dir, 'ok');
            } else {
                $btnPromote.disabled = false;
                $btnPromote.textContent = 'Promote to card repo';
                $promoteMsg.className = 'status-msg pending';
                $promoteMsg.textContent = data.error || 'Promote failed';
                $promoteMsg.hidden = false;
            }
        })
        .catch(function (err) {
            $btnPromote.disabled = false;
            $btnPromote.textContent = 'Promote to card repo';
            $promoteMsg.className = 'status-msg pending';
            $promoteMsg.textContent = 'Error: ' + err.message;
            $promoteMsg.hidden = false;
        });
    });

    // ── Init ────────────────────────────────────────────────────────────

    setStep(1);
    loadStatus();
    loadBranches();
})();
