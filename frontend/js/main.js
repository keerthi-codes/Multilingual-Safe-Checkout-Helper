/**
 * Multilingual Safe-Checkout Helper - Complete Frontend Integration
 * Pure Vanilla JavaScript implementation with zero frontend framework dependencies.
 * 
 * Security & Integrity Guarantees:
 * 1. Safe DOM Rendering: All dynamic untrusted data rendered via textContent / DOM APIs (Zero innerHTML).
 * 2. In-Memory Secrets: Proposal approval_secret is retained strictly in private closure memory and zeroed on use.
 * 3. Authoritative Pricing: Frontend never calculates or assumes final prices; backend remains sole authority.
 * 4. Payment Verification: Never trusts query parameters for payment success; requires verified backend capture.
 */

document.addEventListener("DOMContentLoaded", () => {
    // API Route Endpoints
    const PRODUCTS_API = "/api/v1/products";
    const MANDATE_API = "/api/v1/mandates";
    const AI_API = "/api/v1/ai/chat";
    const PAYMENTS_API = "/api/v1/payments";

    // Application State (In-Memory Only)
    let activeMandate = null;
    let allCategories = [];
    let currentProposalApprovalSecret = null; // Strictly in frontend memory only!
    let activeProposal = null;
    let currentSelectedProduct = null;
    let activeOrderData = null;
    let conversationHistory = [];
    let isRequestPending = false;
    let debounceTimer = null;

    // DOM Elements - Catalogue
    const productsGrid = document.getElementById("products-grid");
    const loadingState = document.getElementById("loading-state");
    const errorState = document.getElementById("error-state");
    const errorDetail = document.getElementById("error-detail");
    const emptyState = document.getElementById("empty-state");
    const searchInput = document.getElementById("search-input");
    const categoryFilter = document.getElementById("category-filter");
    const availabilityFilter = document.getElementById("availability-filter");
    const resetFiltersBtn = document.getElementById("reset-filters");
    const retryBtn = document.getElementById("retry-button");
    const catalogueCount = document.getElementById("catalogue-count");

    // DOM Elements - Mandate
    const quickMandateBtn = document.getElementById("quick-mandate-btn");
    const customMandateToggleBtn = document.getElementById("custom-mandate-toggle-btn");
    const customMandatePanel = document.getElementById("custom-mandate-panel");
    const customMandateForm = document.getElementById("custom-mandate-form");
    const customMandateCancelBtn = document.getElementById("custom-mandate-cancel-btn");
    const mandateAmountInput = document.getElementById("mandate-amount-input");
    const mandateCategoriesContainer = document.getElementById("mandate-categories-container");
    const mandateCategorySelectAllBtn = document.getElementById("mandate-category-select-all");
    const mandateQuantityInput = document.getElementById("mandate-quantity-input");
    const mandateExpirySelect = document.getElementById("mandate-expiry-select");
    const mandateFormError = document.getElementById("mandate-form-error");
    const mandateRefreshBtn = document.getElementById("mandate-refresh-btn");
    const mandateRevokeBtn = document.getElementById("mandate-revoke-btn");

    const mandateIdDisplay = document.getElementById("mandate-id-display");
    const mandateBudgetDisplay = document.getElementById("mandate-budget-display");
    const mandateCategoriesDisplay = document.getElementById("mandate-categories-display");
    const mandateMaxQtyDisplay = document.getElementById("mandate-max-qty-display");
    const mandateExpiryDisplay = document.getElementById("mandate-expiry-display");
    const mandateStatusPill = document.getElementById("mandate-status-pill");

    // DOM Elements - AI Assistant
    const aiChatForm = document.getElementById("ai-chat-form");
    const aiChatInput = document.getElementById("ai-chat-input");
    const aiChatFeed = document.getElementById("ai-chat-feed");
    const aiLanguageSelect = document.getElementById("ai-language-select");
    const aiSendBtn = document.getElementById("ai-send-btn");
    const quickPromptBtns = document.querySelectorAll(".quick-prompt-btn");

    // DOM Elements - Proposal & Evaluation Modal
    const proposalModal = document.getElementById("proposal-modal");
    const proposalModalBackdrop = document.getElementById("proposal-modal-backdrop");
    const proposalModalCloseBtn = document.getElementById("proposal-modal-close-btn");
    const modalProductCategory = document.getElementById("modal-product-category");
    const modalProductName = document.getElementById("modal-product-name");
    const modalProductUnitPrice = document.getElementById("modal-product-unit-price");
    const modalProductDesc = document.getElementById("modal-product-desc");
    const modalQtyInput = document.getElementById("modal-qty-input");
    const modalQtyMinus = document.getElementById("modal-qty-minus");
    const modalQtyPlus = document.getElementById("modal-qty-plus");
    const modalEstimatedTotal = document.getElementById("modal-estimated-total");
    const modalMandateNotice = document.getElementById("modal-mandate-notice");
    const modalMandateIdLabel = document.getElementById("modal-mandate-id-label");
    const modalMandateBudgetLabel = document.getElementById("modal-mandate-budget-label");
    const modalEvaluateBtn = document.getElementById("modal-evaluate-btn");
    const modalEvalActionBox = document.getElementById("modal-eval-action-box");
    const modalEvalResultBox = document.getElementById("modal-eval-result-box");
    const modalPolicyBanner = document.getElementById("modal-policy-banner");
    const modalDecisionLabel = document.getElementById("modal-decision-label");
    const modalReasonCode = document.getElementById("modal-reason-code");
    const modalReasonMessage = document.getElementById("modal-reason-message");
    const modalAuthoritativeBox = document.getElementById("modal-authoritative-box");
    const modalAuthoritativeTotal = document.getElementById("modal-authoritative-total");
    const modalProposalId = document.getElementById("modal-proposal-id");
    const modalApprovalBox = document.getElementById("modal-approval-box");
    const modalApproveBtn = document.getElementById("modal-approve-btn");
    const modalCheckoutStage = document.getElementById("modal-checkout-stage");
    const modalCheckoutContainer = document.getElementById("modal-checkout-container");

    // DOM Elements - Active Checkout Flow Section
    const checkoutFlowPanel = document.getElementById("checkout-flow-panel");
    const checkoutPanelContent = document.getElementById("checkout-panel-content");
    const closeCheckoutPanelBtn = document.getElementById("close-checkout-panel-btn");

    // DOM Elements - Notification Banner
    const notificationBanner = document.getElementById("notification-banner");
    const notificationIcon = document.getElementById("notification-icon");
    const notificationTitle = document.getElementById("notification-title");
    const notificationMessage = document.getElementById("notification-message");
    const notificationActions = document.getElementById("notification-actions");
    const notificationCloseBtn = document.getElementById("notification-close-btn");

    /**
     * Display a global dismissable notification banner safely
     */
    function showNotification(type, title, message, actions = []) {
        notificationBanner.className = "mb-6 p-4 rounded-xl border flex items-start justify-between gap-3 text-xs animate-fade-in";
        if (type === "success") {
            notificationBanner.classList.add("bg-emerald-950/40", "border-emerald-500/40", "text-emerald-200");
            notificationIcon.textContent = "✓";
        } else if (type === "error") {
            notificationBanner.classList.add("bg-red-950/40", "border-red-500/40", "text-red-200");
            notificationIcon.textContent = "✕";
        } else if (type === "warning") {
            notificationBanner.classList.add("bg-amber-950/40", "border-amber-500/40", "text-amber-200");
            notificationIcon.textContent = "⚠️";
        } else {
            notificationBanner.classList.add("bg-indigo-950/40", "border-indigo-500/40", "text-indigo-200");
            notificationIcon.textContent = "ℹ️";
        }

        notificationTitle.textContent = title;
        notificationMessage.textContent = message;

        while (notificationActions.firstChild) {
            notificationActions.removeChild(notificationActions.firstChild);
        }

        actions.forEach(act => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "px-3 py-1 bg-slate-800 hover:bg-slate-700 text-white rounded text-xs font-medium border border-slate-700 transition";
            btn.textContent = act.label;
            btn.addEventListener("click", act.onClick);
            notificationActions.appendChild(btn);
        });

        notificationBanner.classList.remove("hidden");
    }

    notificationCloseBtn.addEventListener("click", () => {
        notificationBanner.classList.add("hidden");
    });

    /**
     * Safely format monetary Decimal amounts in INR
     */
    function formatINR(price) {
        const num = parseFloat(price);
        if (isNaN(num)) return `₹${price} INR`;
        return new Intl.NumberFormat("en-IN", {
            style: "currency",
            currency: "INR",
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format(num);
    }

    /**
     * Format USD amounts
     */
    function formatUSD(amount) {
        const num = parseFloat(amount);
        if (isNaN(num)) return `$${amount} USD`;
        return new Intl.NumberFormat("en-US", {
            style: "currency",
            currency: "USD",
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format(num);
    }

    /**
     * Format UTC Datetime into user local time
     */
    function formatDateTime(isoString) {
        if (!isoString) return "-";
        try {
            const dt = new Date(isoString);
            return dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) + " (" + dt.toLocaleDateString() + ")";
        } catch {
            return isoString;
        }
    }

    /**
     * Set catalogue view state: 'loading' | 'error' | 'empty' | 'content'
     */
    function setCatalogueState(state, errorMessage = "") {
        loadingState.classList.toggle("hidden", state !== "loading");
        errorState.classList.toggle("hidden", state !== "error");
        emptyState.classList.toggle("hidden", state !== "empty");
        productsGrid.classList.toggle("hidden", state !== "content");

        if (state === "error" && errorMessage) {
            errorDetail.textContent = errorMessage;
        }
    }

    /**
     * Create product card safely using DOM methods and textContent to prevent XSS
     */
    function createProductCard(product) {
        const card = document.createElement("article");
        card.className = "flex flex-col bg-slate-900 border border-slate-800 rounded-xl p-5 hover:border-slate-700 transition duration-150 shadow-sm";

        // Badges Row
        const badgeRow = document.createElement("div");
        badgeRow.className = "flex items-center justify-between text-xs mb-3";

        const categoryBadge = document.createElement("span");
        categoryBadge.className = "px-2.5 py-0.5 rounded-full bg-indigo-500/10 text-indigo-400 font-medium border border-indigo-500/20";
        categoryBadge.textContent = product.category;

        const stockBadge = document.createElement("span");
        if (product.available) {
            stockBadge.className = "px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 font-medium border border-emerald-500/20";
            stockBadge.textContent = "In Stock";
        } else {
            stockBadge.className = "px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-400 font-medium border border-slate-700";
            stockBadge.textContent = "Out of Stock";
        }

        badgeRow.appendChild(categoryBadge);
        badgeRow.appendChild(stockBadge);
        card.appendChild(badgeRow);

        // Product Name
        const title = document.createElement("h3");
        title.className = "text-base font-semibold text-white mb-2 leading-snug";
        title.textContent = product.name;
        card.appendChild(title);

        // Product Description
        const desc = document.createElement("p");
        desc.className = "text-xs text-slate-400 mb-4 flex-grow line-clamp-3 leading-relaxed";
        desc.textContent = product.description;
        card.appendChild(desc);

        // Price & Supported Languages Row
        const priceRow = document.createElement("div");
        priceRow.className = "flex items-baseline justify-between pt-3 border-t border-slate-800/80 mb-4";

        const priceWrapper = document.createElement("div");
        priceWrapper.className = "flex flex-col";

        const priceLabel = document.createElement("span");
        priceLabel.className = "text-[10px] uppercase tracking-wider text-slate-500 font-medium";
        priceLabel.textContent = "Price (" + (product.currency || "INR") + ")";

        const priceVal = document.createElement("span");
        priceVal.className = "text-lg font-bold text-white tracking-tight";
        priceVal.textContent = formatINR(product.price);

        priceWrapper.appendChild(priceLabel);
        priceWrapper.appendChild(priceVal);

        const langBadge = document.createElement("span");
        langBadge.className = "text-[10px] text-slate-500 uppercase tracking-wider font-mono";
        langBadge.textContent = (product.supported_languages || []).join(" · ");

        priceRow.appendChild(priceWrapper);
        priceRow.appendChild(langBadge);
        card.appendChild(priceRow);

        // Action Button: Select & Evaluate Purchase
        const actionBtn = document.createElement("button");
        actionBtn.type = "button";

        if (product.available) {
            actionBtn.className = "w-full py-2 bg-indigo-600/30 hover:bg-indigo-600 text-indigo-200 hover:text-white border border-indigo-500/40 rounded-lg text-xs font-semibold transition flex items-center justify-center gap-1.5";
            actionBtn.textContent = "Select & Evaluate Purchase";
            actionBtn.addEventListener("click", () => {
                openProposalModal(product);
            });
        } else {
            actionBtn.className = "w-full py-2 bg-slate-800/60 text-slate-500 border border-slate-800 rounded-lg text-xs font-medium cursor-not-allowed";
            actionBtn.textContent = "Unavailable (Out of Stock)";
            actionBtn.disabled = true;
        }

        card.appendChild(actionBtn);
        return card;
    }

    /**
     * Load unique categories from API and populate filter & mandate form
     */
    async function loadCategories() {
        try {
            const res = await fetch(`${PRODUCTS_API}/categories`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            allCategories = await res.json();

            // Populate catalogue category filter
            while (categoryFilter.options.length > 1) {
                categoryFilter.remove(1);
            }
            allCategories.forEach(cat => {
                const opt = document.createElement("option");
                opt.value = cat;
                opt.textContent = cat;
                categoryFilter.appendChild(opt);
            });

            // Populate custom mandate checkboxes
            populateMandateCategoryCheckboxes();
        } catch (err) {
            console.warn("Could not load categories dropdown:", err);
        }
    }

    /**
     * Populate category checkboxes for custom mandate configuration
     */
    function populateMandateCategoryCheckboxes() {
        while (mandateCategoriesContainer.firstChild) {
            mandateCategoriesContainer.removeChild(mandateCategoriesContainer.firstChild);
        }

        if (allCategories.length === 0) {
            const fallback = document.createElement("span");
            fallback.className = "text-slate-500 text-[11px]";
            fallback.textContent = "No categories loaded";
            mandateCategoriesContainer.appendChild(fallback);
            return;
        }

        allCategories.forEach(cat => {
            const label = document.createElement("label");
            label.className = "flex items-center gap-2 text-[11px] text-slate-300 cursor-pointer hover:text-white";

            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.name = "mandate-category";
            checkbox.value = cat;
            checkbox.checked = true; // default select all
            checkbox.className = "rounded border-slate-700 bg-slate-900 text-indigo-500 focus:ring-0";

            const text = document.createElement("span");
            text.textContent = cat;

            label.appendChild(checkbox);
            label.appendChild(text);
            mandateCategoriesContainer.appendChild(label);
        });
    }

    // Select all / Deselect all categories
    mandateCategorySelectAllBtn.addEventListener("click", () => {
        const checkboxes = mandateCategoriesContainer.querySelectorAll('input[name="mandate-category"]');
        const anyUnchecked = Array.from(checkboxes).some(cb => !cb.checked);
        checkboxes.forEach(cb => {
            cb.checked = anyUnchecked;
        });
        mandateCategorySelectAllBtn.textContent = anyUnchecked ? "Deselect All" : "Select All";
    });

    /**
     * Fetch products from catalogue API using active filters
     */
    async function fetchProducts() {
        setCatalogueState("loading");

        const params = new URLSearchParams();
        const search = searchInput.value.trim();
        const category = categoryFilter.value.trim();
        const availability = availabilityFilter.value.trim();

        if (search) params.set("search", search);
        if (category) params.set("category", category);
        if (availability) params.set("available", availability);

        const url = `${PRODUCTS_API}?${params.toString()}`;

        try {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`Server returned HTTP ${res.status}`);
            const products = await res.json();

            while (productsGrid.firstChild) {
                productsGrid.removeChild(productsGrid.firstChild);
            }

            if (!Array.isArray(products) || products.length === 0) {
                setCatalogueState("empty");
                catalogueCount.textContent = "0 items found";
                return;
            }

            products.forEach(p => {
                productsGrid.appendChild(createProductCard(p));
            });

            catalogueCount.textContent = `${products.length} item${products.length === 1 ? '' : 's'} displayed`;
            setCatalogueState("content");
        } catch (err) {
            setCatalogueState("error", err.message || "Failed to fetch products.");
        }
    }

    /**
     * Update Spending Mandate Details in UI
     */
    function updateMandateUI(mandate) {
        activeMandate = mandate;

        if (!mandate) {
            mandateIdDisplay.textContent = "None";
            mandateIdDisplay.title = "No active mandate";
            mandateBudgetDisplay.textContent = "₹0.00 INR";
            mandateCategoriesDisplay.textContent = "None";
            mandateCategoriesDisplay.title = "No permitted categories";
            mandateMaxQtyDisplay.textContent = "-";
            mandateExpiryDisplay.textContent = "-";
            mandateStatusPill.textContent = "NO MANDATE";
            mandateStatusPill.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-slate-400 font-semibold border border-slate-700";
            mandateRevokeBtn.classList.add("hidden");
            quickMandateBtn.disabled = false;
            quickMandateBtn.textContent = "Quick Demo (₹5,000)";
            quickMandateBtn.className = "flex-1 min-w-[140px] px-3 py-2 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 rounded-lg text-xs font-medium transition";
            return;
        }

        mandateIdDisplay.textContent = mandate.id;
        mandateIdDisplay.title = mandate.id;
        mandateBudgetDisplay.textContent = formatINR(mandate.max_amount);
        mandateCategoriesDisplay.textContent = (mandate.permitted_categories || []).join(", ");
        mandateCategoriesDisplay.title = (mandate.permitted_categories || []).join(", ");
        mandateMaxQtyDisplay.textContent = `${mandate.max_quantity} items max`;
        mandateExpiryDisplay.textContent = formatDateTime(mandate.expires_at);

        // Update status pill styling
        const status = mandate.status || "UNKNOWN";
        mandateStatusPill.textContent = status;

        if (status === "ACTIVE") {
            mandateStatusPill.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-500/20 text-emerald-300 font-semibold border border-emerald-500/40";
            mandateRevokeBtn.classList.remove("hidden");
            quickMandateBtn.textContent = "Mandate Active";
            quickMandateBtn.className = "flex-1 min-w-[140px] px-3 py-2 bg-slate-800 text-slate-400 border border-slate-700 rounded-lg text-xs font-medium cursor-default";
        } else if (status === "RESERVED") {
            mandateStatusPill.textContent = "RESERVED (HELD)";
            mandateStatusPill.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-amber-500/20 text-amber-300 font-semibold border border-amber-500/40";
            mandateRevokeBtn.classList.add("hidden");
        } else if (status === "CONSUMED") {
            mandateStatusPill.textContent = "CONSUMED (PAID)";
            mandateStatusPill.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-purple-500/20 text-purple-300 font-semibold border border-purple-500/40";
            mandateRevokeBtn.classList.add("hidden");
            quickMandateBtn.disabled = false;
            quickMandateBtn.textContent = "Create New Mandate";
            quickMandateBtn.className = "flex-1 min-w-[140px] px-3 py-2 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 rounded-lg text-xs font-medium transition";
        } else if (status === "EXPIRED") {
            mandateStatusPill.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-red-500/20 text-red-300 font-semibold border border-red-500/40";
            mandateRevokeBtn.classList.add("hidden");
            quickMandateBtn.disabled = false;
            quickMandateBtn.textContent = "Create New Mandate";
            quickMandateBtn.className = "flex-1 min-w-[140px] px-3 py-2 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 rounded-lg text-xs font-medium transition";
        } else if (status === "REVOKED") {
            mandateStatusPill.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-slate-700 text-slate-300 font-semibold border border-slate-600";
            mandateRevokeBtn.classList.add("hidden");
            quickMandateBtn.disabled = false;
            quickMandateBtn.textContent = "Create New Mandate";
            quickMandateBtn.className = "flex-1 min-w-[140px] px-3 py-2 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 rounded-lg text-xs font-medium transition";
        }
    }

    /**
     * Create Quick Demo Mandate (₹5,000 for Electronics & Books)
     */
    async function setupDemoMandate() {
        quickMandateBtn.disabled = true;
        quickMandateBtn.textContent = "Establishing mandate...";

        try {
            const res = await fetch(MANDATE_API, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    max_amount: "5000.00",
                    currency: "INR",
                    permitted_categories: ["Electronics", "Books"],
                    max_quantity: 2,
                    expires_in_seconds: 7200,
                }),
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `HTTP ${res.status}`);
            }

            const newMandate = await res.json();
            updateMandateUI(newMandate);
            showNotification("success", "Spending Mandate Active", `Budget of ₹5,000 established for Electronics & Books.`);
            appendChatMessage("assistant", `Spending mandate established! Your budget is ₹5,000 for Electronics & Books. You can now select products or ask for recommendations.`);
        } catch (err) {
            showNotification("error", "Mandate Setup Failed", err.message);
            quickMandateBtn.disabled = false;
            quickMandateBtn.textContent = "Quick Demo (₹5,000)";
        }
    }

    // Toggle custom mandate configuration panel
    customMandateToggleBtn.addEventListener("click", () => {
        customMandatePanel.classList.toggle("hidden");
    });

    customMandateCancelBtn.addEventListener("click", () => {
        customMandatePanel.classList.add("hidden");
        mandateFormError.classList.add("hidden");
    });

    /**
     * Handle Custom Mandate Form Submission
     */
    customMandateForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        mandateFormError.classList.add("hidden");

        const amountVal = parseFloat(mandateAmountInput.value);
        if (isNaN(amountVal) || amountVal <= 0) {
            mandateFormError.textContent = "Please enter a valid spending limit greater than 0.";
            mandateFormError.classList.remove("hidden");
            return;
        }

        const selectedCats = Array.from(mandateCategoriesContainer.querySelectorAll('input[name="mandate-category"]:checked'))
            .map(cb => cb.value.trim());

        if (selectedCats.length === 0) {
            mandateFormError.textContent = "Please select at least one permitted category.";
            mandateFormError.classList.remove("hidden");
            return;
        }

        const qtyVal = parseInt(mandateQuantityInput.value, 10);
        if (isNaN(qtyVal) || qtyVal < 1) {
            mandateFormError.textContent = "Maximum quantity must be at least 1.";
            mandateFormError.classList.remove("hidden");
            return;
        }

        const expiryVal = parseInt(mandateExpirySelect.value, 10);

        const submitBtn = document.getElementById("create-mandate-submit-btn");
        submitBtn.disabled = true;
        submitBtn.textContent = "Creating...";

        try {
            const res = await fetch(MANDATE_API, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    max_amount: amountVal.toFixed(2),
                    currency: "INR",
                    permitted_categories: selectedCats,
                    max_quantity: qtyVal,
                    expires_in_seconds: expiryVal,
                }),
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `HTTP ${res.status}`);
            }

            const newMandate = await res.json();
            updateMandateUI(newMandate);
            customMandatePanel.classList.add("hidden");
            showNotification("success", "Custom Mandate Established", `Budget: ${formatINR(newMandate.max_amount)}, Categories: ${newMandate.permitted_categories.join(", ")}`);
            appendChatMessage("assistant", `Custom spending mandate active! Budget is ${formatINR(newMandate.max_amount)} for categories [${newMandate.permitted_categories.join(", ")}].`);
        } catch (err) {
            mandateFormError.textContent = "Failed to establish mandate: " + err.message;
            mandateFormError.classList.remove("hidden");
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = "Establish Mandate";
        }
    });

    /**
     * Refresh Active Mandate Status from Backend
     */
    async function refreshMandateStatus() {
        if (!activeMandate || !activeMandate.id) {
            showNotification("info", "No Mandate Active", "Create a spending mandate to inspect its status.");
            return;
        }

        mandateRefreshBtn.classList.add("animate-spin");
        try {
            const res = await fetch(`${MANDATE_API}/${activeMandate.id}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const freshMandate = await res.json();
            updateMandateUI(freshMandate);
            showNotification("info", "Mandate Status Refreshed", `Current status: ${freshMandate.status}`);
        } catch (err) {
            showNotification("error", "Refresh Failed", err.message);
        } finally {
            mandateRefreshBtn.classList.remove("animate-spin");
        }
    }

    mandateRefreshBtn.addEventListener("click", refreshMandateStatus);

    /**
     * Revoke Active Mandate
     */
    async function revokeCurrentMandate() {
        if (!activeMandate || !activeMandate.id) return;
        if (!confirm("Are you sure you want to revoke this spending mandate? Revoked mandates cannot approve purchases.")) return;

        mandateRevokeBtn.disabled = true;
        try {
            const res = await fetch(`${MANDATE_API}/${activeMandate.id}/revoke`, {
                method: "POST",
            });
            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `HTTP ${res.status}`);
            }
            const revoked = await res.json();
            updateMandateUI(revoked);
            showNotification("warning", "Mandate Revoked", "The spending mandate was revoked. No further purchases can be evaluated or approved against it.");
            appendChatMessage("assistant", "Your spending mandate has been revoked. You must create a new mandate to proceed with purchases.");
        } catch (err) {
            showNotification("error", "Revocation Failed", err.message);
        } finally {
            mandateRevokeBtn.disabled = false;
        }
    }

    mandateRevokeBtn.addEventListener("click", revokeCurrentMandate);

    /**
     * Append a message to the AI Chat Feed safely using DOM APIs & textContent
     */
    function appendChatMessage(role, text) {
        const bubble = document.createElement("div");
        if (role === "user") {
            bubble.className = "p-3 rounded-xl bg-indigo-600/30 border border-indigo-500/40 text-indigo-100 ml-8 leading-relaxed animate-fade-in";
        } else {
            bubble.className = "p-3 rounded-xl bg-slate-800/80 border border-slate-700/60 text-slate-200 mr-8 leading-relaxed animate-fade-in";
        }
        bubble.textContent = text;
        aiChatFeed.appendChild(bubble);
        aiChatFeed.scrollTop = aiChatFeed.scrollHeight;
    }

    /**
     * Render Proposal Card inside the AI Chat Feed
     */
    function renderChatProposalCard(proposal, policyResult, approvalSecret) {
        const card = document.createElement("div");
        card.className = "p-4 rounded-xl bg-slate-950 border border-indigo-500/40 mr-6 my-2 shadow-lg space-y-3 animate-fade-in";

        // Header with decision
        const topRow = document.createElement("div");
        topRow.className = "flex items-center justify-between";

        const title = document.createElement("span");
        title.className = "text-xs font-bold uppercase tracking-wider text-indigo-400";
        title.textContent = "AI Purchase Recommendation";

        const badge = document.createElement("span");
        if (policyResult.decision === "ALLOW_PENDING_APPROVAL") {
            badge.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-500/20 text-emerald-400 font-semibold border border-emerald-500/30";
            badge.textContent = "POLICY ALLOWED";
        } else {
            badge.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-red-500/20 text-red-400 font-semibold border border-red-500/30";
            badge.textContent = "BLOCKED BY POLICY";
        }

        topRow.appendChild(title);
        topRow.appendChild(badge);
        card.appendChild(topRow);

        // Product info
        const info = document.createElement("div");
        info.className = "space-y-1";

        const pName = document.createElement("p");
        pName.className = "text-sm font-semibold text-white";
        pName.textContent = proposal.product_name || policyResult.product_id;
        info.appendChild(pName);

        const pQtyPrice = document.createElement("p");
        pQtyPrice.className = "text-xs text-slate-300 font-mono";
        pQtyPrice.textContent = `Quantity: ${policyResult.quantity} · Authoritative Total: ${formatINR(policyResult.total_amount || proposal.total_amount || 0)}`;
        info.appendChild(pQtyPrice);

        const reason = document.createElement("p");
        reason.className = "text-[11px] text-slate-400 italic";
        reason.textContent = policyResult.reason_message;
        info.appendChild(reason);

        card.appendChild(info);

        // Approval Action if allowed
        if (policyResult.decision === "ALLOW_PENDING_APPROVAL" && approvalSecret) {
            // Keep approval secret in local memory closure
            let secretInMemory = approvalSecret;
            currentProposalApprovalSecret = approvalSecret;
            activeProposal = proposal;

            const approveBtn = document.createElement("button");
            approveBtn.type = "button";
            approveBtn.className = "w-full py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-semibold shadow transition";
            approveBtn.textContent = "Explicitly Approve Purchase (One-Time)";

            const checkoutSubContainer = document.createElement("div");
            checkoutSubContainer.className = "hidden pt-2 border-t border-slate-800 space-y-2";

            approveBtn.addEventListener("click", async () => {
                if (!secretInMemory) return;
                approveBtn.disabled = true;
                approveBtn.textContent = "Verifying cryptographic secret...";

                try {
                    const res = await fetch(`${MANDATE_API}/proposals/${proposal.id}/approve`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ approval_secret: secretInMemory }),
                    });

                    if (!res.ok) {
                        const err = await res.json().catch(() => ({}));
                        throw new Error(err.detail || `HTTP ${res.status}`);
                    }

                    // Zero out raw secret immediately!
                    secretInMemory = null;
                    currentProposalApprovalSecret = null;

                    approveBtn.textContent = "✓ USER APPROVED (Mandate Reserved)";
                    approveBtn.className = "w-full py-2 bg-emerald-950/60 text-emerald-300 border border-emerald-500/40 rounded-lg text-xs font-semibold cursor-default";

                    // Update active mandate status to RESERVED
                    if (activeMandate) {
                        activeMandate.status = "RESERVED";
                        updateMandateUI(activeMandate);
                    }

                    // Render checkout controls directly in the card
                    checkoutSubContainer.classList.remove("hidden");
                    renderCheckoutFlow(checkoutSubContainer, proposal.id);

                    // Also reveal top checkout panel
                    showActiveCheckoutSection(proposal.id);
                } catch (err) {
                    approveBtn.disabled = false;
                    approveBtn.textContent = "Retry Approval";
                    showNotification("error", "Approval Rejected", err.message);
                }
            });

            card.appendChild(approveBtn);
            card.appendChild(checkoutSubContainer);
        } else if (policyResult.decision === "BLOCKED") {
            const blockedNote = document.createElement("div");
            blockedNote.className = "text-[11px] p-2 rounded bg-red-950/30 text-red-300 border border-red-800/30";
            blockedNote.textContent = `Reason: [${policyResult.reason_code}] ${policyResult.reason_message}`;
            card.appendChild(blockedNote);
        }

        aiChatFeed.appendChild(card);
        aiChatFeed.scrollTop = aiChatFeed.scrollHeight;
    }

    /**
     * Send AI Chat Message
     */
    async function handleAIChatSubmit(e) {
        if (e) e.preventDefault();
        const text = aiChatInput.value.trim();
        if (!text || isRequestPending) return;

        isRequestPending = true;
        const lang = aiLanguageSelect.value;
        appendChatMessage("user", text);
        aiChatInput.value = "";
        aiSendBtn.disabled = true;
        aiChatInput.disabled = true;
        aiSendBtn.innerHTML = `<span>Thinking...</span>`;

        conversationHistory.push({ role: "user", content: text });

        try {
            const body = {
                message: text,
                language: lang,
                conversation_history: conversationHistory.slice(-10),
            };
            if (activeMandate && activeMandate.status === "ACTIVE") {
                body.mandate_id = activeMandate.id;
            }

            const res = await fetch(AI_API, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `HTTP ${res.status}`);
            }

            const data = await res.json();
            appendChatMessage("assistant", data.reply_text);
            conversationHistory.push({ role: "assistant", content: data.reply_text });

            // If proposal evaluation was executed by policy engine
            if (data.evaluated_policy_result && data.evaluated_policy_result.proposal) {
                renderChatProposalCard(
                    data.evaluated_policy_result.proposal,
                    data.evaluated_policy_result,
                    data.evaluated_policy_result.approval_secret
                );
            } else if (data.evaluated_policy_result && data.evaluated_policy_result.decision === "BLOCKED") {
                renderChatProposalCard(
                    { product_name: data.evaluated_policy_result.product_id },
                    data.evaluated_policy_result,
                    null
                );
            } else if (data.recommended_proposal && !activeMandate) {
                // Recommendation made without active mandate
                const mandatePromptCard = document.createElement("div");
                mandatePromptCard.className = "p-3 rounded-xl bg-amber-950/20 border border-amber-500/30 mr-6 my-2 text-xs space-y-2";
                
                const note = document.createElement("p");
                note.className = "text-amber-200";
                note.textContent = `AI recommended product '${data.recommended_proposal.product_id}' (Qty: ${data.recommended_proposal.quantity}), but no spending mandate is currently active. Establish a mandate to evaluate and approve this purchase.`;
                mandatePromptCard.appendChild(note);

                const quickBtn = document.createElement("button");
                quickBtn.type = "button";
                quickBtn.className = "px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded font-medium transition text-xs";
                quickBtn.textContent = "Establish Spending Mandate";
                quickBtn.addEventListener("click", () => {
                    setupDemoMandate();
                });
                mandatePromptCard.appendChild(quickBtn);

                aiChatFeed.appendChild(mandatePromptCard);
                aiChatFeed.scrollTop = aiChatFeed.scrollHeight;
            }
        } catch (err) {
            appendChatMessage("assistant", "Unable to connect to the shopping assistant: " + err.message);
        } finally {
            isRequestPending = false;
            aiSendBtn.disabled = false;
            aiChatInput.disabled = false;
            aiSendBtn.textContent = "Send";
            aiChatInput.focus();
        }
    }

    // Quick prompt buttons
    quickPromptBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            const prompt = btn.getAttribute("data-prompt");
            const lang = btn.getAttribute("data-lang");
            if (lang) aiLanguageSelect.value = lang;
            aiChatInput.value = prompt;
            handleAIChatSubmit();
        });
    });

    /**
     * Open Proposal Modal for a chosen Catalogue Product
     */
    function openProposalModal(product) {
        currentSelectedProduct = product;
        currentProposalApprovalSecret = null; // reset memory secret

        modalProductCategory.textContent = product.category;
        modalProductName.textContent = product.name;
        modalProductUnitPrice.textContent = formatINR(product.price);
        modalProductDesc.textContent = product.description;

        modalQtyInput.value = 1;
        updateModalCalculations();

        // Update Mandate check indicator
        if (activeMandate && activeMandate.status === "ACTIVE") {
            modalMandateIdLabel.textContent = activeMandate.id;
            modalMandateBudgetLabel.textContent = formatINR(activeMandate.max_amount);
            modalMandateNotice.className = "p-2.5 rounded-lg text-[11px] bg-slate-950 border border-slate-800 text-slate-400 flex items-center justify-between";
            modalEvaluateBtn.disabled = false;
            modalEvaluateBtn.textContent = "Evaluate Against Spending Mandate";
        } else if (activeMandate && activeMandate.status !== "ACTIVE") {
            modalMandateIdLabel.textContent = `${activeMandate.id} (${activeMandate.status})`;
            modalMandateBudgetLabel.textContent = formatINR(activeMandate.max_amount);
            modalMandateNotice.className = "p-2.5 rounded-lg text-[11px] bg-amber-950/20 border border-amber-500/30 text-amber-300 flex items-center justify-between";
            modalEvaluateBtn.disabled = false;
            modalEvaluateBtn.textContent = "Evaluate (Mandate Not Active: Will Block)";
        } else {
            modalMandateIdLabel.textContent = "No Mandate";
            modalMandateBudgetLabel.textContent = "₹0.00";
            modalMandateNotice.className = "p-2.5 rounded-lg text-[11px] bg-red-950/20 border border-red-500/30 text-red-300 flex items-center justify-between";
            modalEvaluateBtn.disabled = false;
            modalEvaluateBtn.textContent = "Evaluate (No Mandate: Will Block)";
        }

        // Reset step 2 & 3 views
        modalEvalResultBox.classList.add("hidden");
        modalApprovalBox.classList.add("hidden");
        modalCheckoutStage.classList.add("hidden");
        modalEvalActionBox.classList.remove("hidden");

        proposalModal.classList.remove("hidden");
        document.body.classList.add("overflow-hidden");
    }

    /**
     * Close Proposal Modal safely
     */
    function closeProposalModal() {
        proposalModal.classList.add("hidden");
        document.body.classList.remove("overflow-hidden");
    }

    proposalModalCloseBtn.addEventListener("click", closeProposalModal);
    proposalModalBackdrop.addEventListener("click", closeProposalModal);

    window.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !proposalModal.classList.contains("hidden")) {
            closeProposalModal();
        }
    });

    /**
     * Stepper Quantity calculations in Modal
     */
    function updateModalCalculations() {
        if (!currentSelectedProduct) return;
        const qty = parseInt(modalQtyInput.value, 10) || 1;
        const unit = parseFloat(currentSelectedProduct.price) || 0;
        const total = unit * qty;
        modalEstimatedTotal.textContent = formatINR(total.toFixed(2));
    }

    modalQtyMinus.addEventListener("click", () => {
        let val = parseInt(modalQtyInput.value, 10) || 1;
        if (val > 1) {
            modalQtyInput.value = val - 1;
            updateModalCalculations();
        }
    });

    modalQtyPlus.addEventListener("click", () => {
        let val = parseInt(modalQtyInput.value, 10) || 1;
        if (val < 10) {
            modalQtyInput.value = val + 1;
            updateModalCalculations();
        }
    });

    modalQtyInput.addEventListener("input", () => {
        let val = parseInt(modalQtyInput.value, 10);
        if (isNaN(val) || val < 1) val = 1;
        if (val > 10) val = 10;
        modalQtyInput.value = val;
        updateModalCalculations();
    });

    /**
     * Step 1: Evaluate purchase proposal against active mandate
     */
    modalEvaluateBtn.addEventListener("click", async () => {
        if (!currentSelectedProduct) return;

        const qty = parseInt(modalQtyInput.value, 10) || 1;
        modalEvaluateBtn.disabled = true;
        modalEvaluateBtn.textContent = "Evaluating Policy Constraints...";

        const mandateId = activeMandate ? activeMandate.id : "NO_MANDATE";
        const evalUrl = `${MANDATE_API}/${mandateId}/evaluate`;

        try {
            const res = await fetch(evalUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    product_id: currentSelectedProduct.id,
                    quantity: qty,
                }),
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `HTTP ${res.status}`);
            }

            const evalResult = await res.json();
            renderModalEvaluationResult(evalResult);
        } catch (err) {
            alert("Policy Evaluation failed: " + err.message);
            modalEvaluateBtn.disabled = false;
            modalEvaluateBtn.textContent = "Evaluate Against Spending Mandate";
        }
    });

    /**
     * Step 2: Render Evaluation Result in Modal
     */
    function renderModalEvaluationResult(result) {
        modalEvalResultBox.classList.remove("hidden");
        modalDecisionLabel.textContent = result.decision;
        modalReasonCode.textContent = result.reason_code;
        modalReasonMessage.textContent = result.reason_message;

        if (result.decision === "ALLOW_PENDING_APPROVAL") {
            modalPolicyBanner.className = "p-3 rounded-xl border space-y-1 bg-emerald-950/20 border-emerald-500/40 text-emerald-300";
            modalReasonCode.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";

            modalAuthoritativeTotal.textContent = formatINR(result.total_amount);
            modalProposalId.textContent = result.proposal ? result.proposal.id : "-";
            modalAuthoritativeBox.classList.remove("hidden");

            // Step 3: Reveal explicit user approval
            if (result.approval_secret && result.proposal) {
                // Store secret in memory variable ONLY
                currentProposalApprovalSecret = result.approval_secret;
                activeProposal = result.proposal;

                modalApprovalBox.classList.remove("hidden");
                modalApproveBtn.disabled = false;
                modalApproveBtn.textContent = "Explicitly Approve Purchase (One-Time)";
            }
        } else {
            modalPolicyBanner.className = "p-3 rounded-xl border space-y-1 bg-red-950/20 border-red-500/40 text-red-300";
            modalReasonCode.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-red-500/20 text-red-300 border border-red-500/30";
            modalAuthoritativeBox.classList.add("hidden");
            modalApprovalBox.classList.add("hidden");
            modalCheckoutStage.classList.add("hidden");
        }
    }

    /**
     * Step 3: Explicit User Approval with One-Time Secret
     */
    modalApproveBtn.addEventListener("click", async () => {
        if (!currentProposalApprovalSecret || !activeProposal) {
            alert("No valid approval secret in memory.");
            return;
        }

        modalApproveBtn.disabled = true;
        modalApproveBtn.textContent = "Verifying cryptographic secret...";

        const secretToSubmit = currentProposalApprovalSecret;

        try {
            const res = await fetch(`${MANDATE_API}/proposals/${activeProposal.id}/approve`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ approval_secret: secretToSubmit }),
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${res.status}`);
            }

            const approvedProposal = await res.json();
            activeProposal = approvedProposal;

            // Zero out raw approval secret immediately!
            currentProposalApprovalSecret = null;

            modalApproveBtn.textContent = "✓ USER APPROVED (Mandate Reserved)";
            modalApproveBtn.className = "w-full py-2.5 bg-emerald-950/60 text-emerald-300 border border-emerald-500/40 rounded-xl text-xs font-bold cursor-default";

            // Update mandate state to RESERVED
            if (activeMandate) {
                activeMandate.status = "RESERVED";
                updateMandateUI(activeMandate);
            }

            // Step 4: Render PayPal Checkout inside Modal
            modalCheckoutStage.classList.remove("hidden");
            renderCheckoutFlow(modalCheckoutContainer, approvedProposal.id);

            // Also render in top dashboard
            showActiveCheckoutSection(approvedProposal.id);
        } catch (err) {
            alert("Approval failed: " + err.message);
            modalApproveBtn.disabled = false;
            modalApproveBtn.textContent = "Retry Explicit Approval";
        }
    });

    /**
     * Render Active PayPal Checkout Section on the main dashboard
     */
    function showActiveCheckoutSection(proposalId) {
        checkoutFlowPanel.classList.remove("hidden");
        renderCheckoutFlow(checkoutPanelContent, proposalId);
        checkoutFlowPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    closeCheckoutPanelBtn.addEventListener("click", () => {
        checkoutFlowPanel.classList.add("hidden");
    });

    /**
     * Render PayPal Sandbox Checkout Component in a given container
     */
    function renderCheckoutFlow(container, proposalId) {
        while (container.firstChild) {
            container.removeChild(container.firstChild);
        }

        const box = document.createElement("div");
        box.className = "p-4 rounded-xl bg-slate-950 border border-slate-800 space-y-3";

        const title = document.createElement("h4");
        title.className = "text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center gap-2";
        title.innerHTML = `<span>💳</span> PayPal Sandbox Dual-Currency Checkout`;
        box.appendChild(title);

        const initBtn = document.createElement("button");
        initBtn.type = "button";
        initBtn.className = "w-full py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-semibold shadow transition flex items-center justify-center gap-2";
        initBtn.textContent = "Prepare PayPal Sandbox Order";

        const errorMsg = document.createElement("p");
        errorMsg.className = "text-xs text-red-400 hidden mt-1";

        initBtn.addEventListener("click", async () => {
            initBtn.disabled = true;
            initBtn.textContent = "Creating Sandbox Order...";
            errorMsg.classList.add("hidden");

            try {
                const res = await fetch(`${PAYMENTS_API}/orders/create`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ proposal_id: proposalId })
                });

                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || `HTTP ${res.status}`);
                }

                const orderData = await res.json();
                activeOrderData = orderData;
                renderActiveOrderView(container, orderData, proposalId);
            } catch (err) {
                errorMsg.textContent = "Failed to create PayPal order: " + err.message;
                errorMsg.classList.remove("hidden");
                initBtn.disabled = false;
                initBtn.textContent = "Retry Creating Sandbox Order";
            }
        });

        box.appendChild(initBtn);
        box.appendChild(errorMsg);
        container.appendChild(box);
    }

    /**
     * Render Active PayPal Order Breakdown & Execution Controls
     */
    function renderActiveOrderView(container, orderData, proposalId) {
        while (container.firstChild) {
            container.removeChild(container.firstChild);
        }

        const breakdown = orderData.currency_breakdown;

        const wrapper = document.createElement("div");
        wrapper.className = "p-4 rounded-xl bg-slate-950 border border-slate-800 space-y-3";

        // Header with Order ID
        const hRow = document.createElement("div");
        hRow.className = "flex justify-between items-center pb-2 border-b border-slate-800";
        
        const hTitle = document.createElement("span");
        hTitle.className = "font-bold text-white text-xs";
        hTitle.textContent = "PayPal Sandbox Order Details";

        const orderPill = document.createElement("span");
        orderPill.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-indigo-500/20 text-indigo-300 font-semibold";
        orderPill.textContent = orderData.order_id;

        hRow.appendChild(hTitle);
        hRow.appendChild(orderPill);
        wrapper.appendChild(hRow);

        // Dual-currency breakdown grid
        const grid = document.createElement("div");
        grid.className = "space-y-1.5 font-mono text-xs";

        const inrRow = document.createElement("div");
        inrRow.className = "flex justify-between text-slate-400";
        const inrLabel = document.createElement("span");
        inrLabel.textContent = "Authoritative INR Total:";
        const inrVal = document.createElement("span");
        inrVal.className = "text-white font-bold";
        inrVal.textContent = formatINR(breakdown.inr_total);
        inrRow.appendChild(inrLabel);
        inrRow.appendChild(inrVal);

        const usdRow = document.createElement("div");
        usdRow.className = "flex justify-between text-slate-400";
        const usdLabel = document.createElement("span");
        usdLabel.textContent = "PayPal USD Total (Test Amount):";
        const usdVal = document.createElement("span");
        usdVal.className = "text-emerald-400 font-bold";
        usdVal.textContent = formatUSD(breakdown.usd_amount);
        usdRow.appendChild(usdLabel);
        usdRow.appendChild(usdVal);

        const rateRow = document.createElement("div");
        rateRow.className = "flex justify-between text-slate-500 text-[11px]";
        const rateLabel = document.createElement("span");
        rateLabel.textContent = "Configured Demo Rate:";
        const rateVal = document.createElement("span");
        rateVal.textContent = `1 INR = ${breakdown.exchange_rate} USD`;
        rateRow.appendChild(rateLabel);
        rateRow.appendChild(rateVal);

        grid.appendChild(inrRow);
        grid.appendChild(usdRow);
        grid.appendChild(rateRow);
        wrapper.appendChild(grid);

        // Disclaimer Notice
        const discBox = document.createElement("p");
        discBox.className = "text-[11px] text-amber-300/80 italic pt-1 border-t border-slate-800/80";
        discBox.textContent = breakdown.disclaimer;
        wrapper.appendChild(discBox);

        // Status Banner
        const statusBanner = document.createElement("div");
        statusBanner.className = "text-center text-xs py-2 px-3 rounded font-mono bg-slate-900 border border-slate-700 text-slate-300 font-semibold";
        statusBanner.textContent = `Payment Status: ${orderData.status}`;
        wrapper.appendChild(statusBanner);

        // Approval Link (Opens PayPal Sandbox in new window)
        const payLink = document.createElement("a");
        payLink.href = orderData.approve_url;
        payLink.target = "_blank";
        payLink.rel = "noopener noreferrer";
        payLink.className = "block text-center py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-bold transition shadow";
        payLink.textContent = "Open PayPal Sandbox Approval Window ↗";
        wrapper.appendChild(payLink);

        // Action Buttons Row: Capture, Check Status, Cancel
        const actionRow = document.createElement("div");
        actionRow.className = "grid grid-cols-3 gap-2";

        const captureBtn = document.createElement("button");
        captureBtn.type = "button";
        captureBtn.className = "py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-semibold transition disabled:opacity-50";
        captureBtn.textContent = "Verify & Capture";

        const statusBtn = document.createElement("button");
        statusBtn.type = "button";
        statusBtn.className = "py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-semibold transition border border-slate-700";
        statusBtn.textContent = "Check Status";

        const cancelBtn = document.createElement("button");
        cancelBtn.type = "button";
        cancelBtn.className = "py-2 bg-red-950/40 hover:bg-red-900/40 text-red-300 border border-red-800/40 rounded-lg text-xs font-semibold transition disabled:opacity-50";
        cancelBtn.textContent = "Cancel";

        actionRow.appendChild(captureBtn);
        actionRow.appendChild(statusBtn);
        actionRow.appendChild(cancelBtn);
        wrapper.appendChild(actionRow);

        const resultNotice = document.createElement("div");
        resultNotice.className = "text-xs hidden p-2.5 rounded";
        wrapper.appendChild(resultNotice);

        // Capture Action
        captureBtn.addEventListener("click", async () => {
            captureBtn.disabled = true;
            cancelBtn.disabled = true;
            statusBtn.disabled = true;
            captureBtn.textContent = "Capturing...";
            resultNotice.classList.add("hidden");

            try {
                const res = await fetch(`${PAYMENTS_API}/orders/${orderData.order_id}/capture`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" }
                });

                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || `HTTP ${res.status}`);
                }

                const capResult = await res.json();
                if (capResult.status === "COMPLETED") {
                    statusBanner.className = "text-center text-xs py-2 px-3 rounded font-mono bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-bold";
                    statusBanner.textContent = `✓ PAID: Verified Capture ID ${capResult.capture_id}`;

                    // Update mandate pill to CONSUMED
                    if (activeMandate) {
                        activeMandate.status = "CONSUMED";
                        updateMandateUI(activeMandate);
                    }

                    payLink.remove();
                    actionRow.remove();

                    showNotification("success", "Payment Completed & Verified", `Captured ${formatUSD(capResult.amount_captured_usd)} USD. Capture ID: ${capResult.capture_id}. Mandate is now CONSUMED.`);
                    appendChatMessage("assistant", `🎉 Payment verified and completed! Capture ID: ${capResult.capture_id}. The spending mandate has been finalized to CONSUMED.`);
                } else {
                    throw new Error(`Unexpected capture status: ${capResult.status}`);
                }
            } catch (err) {
                resultNotice.className = "text-xs p-2.5 rounded bg-red-500/20 text-red-300 border border-red-500/30 block";
                resultNotice.textContent = "Capture failed: " + err.message;
                captureBtn.disabled = false;
                cancelBtn.disabled = false;
                statusBtn.disabled = false;
                captureBtn.textContent = "Retry Capture";
            }
        });

        // Check Status Action
        statusBtn.addEventListener("click", async () => {
            statusBtn.disabled = true;
            try {
                const res = await fetch(`${PAYMENTS_API}/orders/${orderData.order_id}`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const record = await res.json();
                statusBanner.textContent = `Payment Status: ${record.status}`;

                if (record.status === "COMPLETED") {
                    statusBanner.className = "text-center text-xs py-2 px-3 rounded font-mono bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-bold";
                    statusBanner.textContent = `✓ PAID: Capture ID ${record.capture_id}`;
                    payLink.remove();
                    actionRow.remove();
                    if (activeMandate) {
                        activeMandate.status = "CONSUMED";
                        updateMandateUI(activeMandate);
                    }
                }
            } catch (err) {
                alert("Failed to fetch order status: " + err.message);
            } finally {
                statusBtn.disabled = false;
            }
        });

        // Cancel Action
        cancelBtn.addEventListener("click", async () => {
            if (!confirm("Are you sure you want to cancel this checkout? The mandate reservation will be safely released.")) return;

            cancelBtn.disabled = true;
            captureBtn.disabled = true;
            statusBtn.disabled = true;
            cancelBtn.textContent = "Cancelling...";

            try {
                const res = await fetch(`${PAYMENTS_API}/orders/${orderData.order_id}/cancel`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" }
                });

                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || `HTTP ${res.status}`);
                }

                statusBanner.className = "text-center text-xs py-2 px-3 rounded font-mono bg-slate-800 text-slate-400 border border-slate-700";
                statusBanner.textContent = `ORDER CANCELLED (Mandate Released)`;

                // Update mandate pill back to ACTIVE
                if (activeMandate) {
                    activeMandate.status = "ACTIVE";
                    updateMandateUI(activeMandate);
                }

                payLink.remove();
                actionRow.remove();

                showNotification("info", "Checkout Cancelled", "PayPal checkout was cancelled. Mandate reservation was safely released back to ACTIVE.");
                appendChatMessage("assistant", `PayPal checkout was cancelled. Your spending mandate reservation has been released back to ACTIVE.`);
            } catch (err) {
                resultNotice.className = "text-xs p-2.5 rounded bg-red-500/20 text-red-300 border border-red-500/30 block";
                resultNotice.textContent = "Cancellation failed: " + err.message;
                cancelBtn.disabled = false;
                captureBtn.disabled = false;
                statusBtn.disabled = false;
                cancelBtn.textContent = "Cancel";
            }
        });

        container.appendChild(wrapper);
    }

    /**
     * Handle PayPal Return / Cancel Navigation Safely
     * NEVER marks payment successful based solely on URL query parameter!
     */
    async function handlePayPalReturnUrl() {
        const urlParams = new URLSearchParams(window.location.search);
        const paymentParam = urlParams.get("payment");
        const tokenParam = urlParams.get("token");

        if (!paymentParam) return;

        // Clean query parameters from URL history without page reload
        const cleanUrl = window.location.pathname;
        window.history.replaceState({}, document.title, cleanUrl);

        if (paymentParam === "success") {
            showNotification(
                "info",
                "Buyer Returned from PayPal Sandbox",
                `Redirect received (Token: ${tokenParam || 'none'}). Verifying capture with backend...`
            );

            // If we have token, attempt capture via backend verification
            if (tokenParam) {
                try {
                    const res = await fetch(`${PAYMENTS_API}/orders/${tokenParam}/capture`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" }
                    });

                    if (res.ok) {
                        const capResult = await res.json();
                        if (capResult.status === "COMPLETED") {
                            showNotification(
                                "success",
                                "Payment Verified & Completed",
                                `Verified Capture ID: ${capResult.capture_id}. Amount: ${formatUSD(capResult.amount_captured_usd)} USD. Mandate finalized to CONSUMED.`
                            );
                            if (activeMandate) {
                                activeMandate.status = "CONSUMED";
                                updateMandateUI(activeMandate);
                            }
                            appendChatMessage("assistant", `🎉 Payment verified and completed! Capture ID: ${capResult.capture_id}.`);
                            return;
                        }
                    }
                } catch {
                    // Fall through to notification
                }
            }

            showNotification(
                "warning",
                "Payment Capture Pending",
                "You have returned from PayPal. Click 'Verify & Capture' on your active order to finalize payment."
            );
        } else if (paymentParam === "cancel") {
            showNotification(
                "warning",
                "PayPal Checkout Cancelled",
                "The PayPal Sandbox session was cancelled. If an order was pending, you can cancel it to release the mandate hold."
            );
            if (tokenParam) {
                // Safely cancel order to release mandate
                try {
                    await fetch(`${PAYMENTS_API}/orders/${tokenParam}/cancel`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" }
                    });
                    if (activeMandate) {
                        activeMandate.status = "ACTIVE";
                        updateMandateUI(activeMandate);
                    }
                } catch {
                    // ignore
                }
            }
        }
    }

    // Event Listeners for Catalogue Filtering
    searchInput.addEventListener("input", () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(fetchProducts, 250);
    });

    categoryFilter.addEventListener("change", fetchProducts);
    availabilityFilter.addEventListener("change", fetchProducts);

    resetFiltersBtn.addEventListener("click", () => {
        searchInput.value = "";
        categoryFilter.value = "";
        availabilityFilter.value = "";
        fetchProducts();
    });

    if (retryBtn) {
        retryBtn.addEventListener("click", () => {
            loadCategories();
            fetchProducts();
        });
    }

    if (quickMandateBtn) {
        quickMandateBtn.addEventListener("click", setupDemoMandate);
    }

    if (aiChatForm) {
        aiChatForm.addEventListener("submit", handleAIChatSubmit);
    }

    // Initial Execution
    loadCategories();
    fetchProducts();
    handlePayPalReturnUrl();
});
