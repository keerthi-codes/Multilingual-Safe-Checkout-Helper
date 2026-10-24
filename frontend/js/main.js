/**
 * Multilingual Safe-Checkout Helper - Frontend Logic
 * Pure Vanilla JavaScript implementation with safe DOM text node rendering (Zero innerHTML with untrusted data).
 */

document.addEventListener("DOMContentLoaded", () => {
    const API_BASE = "/api/v1/products";
    const MANDATE_API = "/api/v1/mandates";
    const AI_API = "/api/v1/ai/chat";
    const PAYMENTS_API = "/api/v1/payments";

    // Catalogue DOM Elements
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

    // Mandate DOM Elements
    const quickMandateBtn = document.getElementById("quick-mandate-btn");
    const mandateIdDisplay = document.getElementById("mandate-id-display");
    const mandateBudgetDisplay = document.getElementById("mandate-budget-display");
    const mandateCategoriesDisplay = document.getElementById("mandate-categories-display");
    const mandateStatusPill = document.getElementById("mandate-status-pill");

    // AI Assistant DOM Elements
    const aiChatForm = document.getElementById("ai-chat-form");
    const aiChatInput = document.getElementById("ai-chat-input");
    const aiChatFeed = document.getElementById("ai-chat-feed");
    const aiLanguageSelect = document.getElementById("ai-language-select");
    const aiSendBtn = document.getElementById("ai-send-btn");

    let activeMandate = null;
    let debounceTimer = null;
    let conversationHistory = [];

    /**
     * Format Decimal price into INR currency format safely
     */
    function formatINR(price) {
        const num = parseFloat(price);
        if (isNaN(num)) return `₹${price}`;
        return new Intl.NumberFormat("en-IN", {
            style: "currency",
            currency: "INR",
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format(num);
    }

    /**
     * Set view state: 'loading' | 'error' | 'empty' | 'content'
     */
    function setState(state, errorMessage = "") {
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

        // Category & Availability Badges Row
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

        // Footer Row with Price and Language Info
        const footerRow = document.createElement("div");
        footerRow.className = "flex items-baseline justify-between pt-3 border-t border-slate-800/80 mt-auto";

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

        footerRow.appendChild(priceWrapper);
        footerRow.appendChild(langBadge);
        card.appendChild(footerRow);

        return card;
    }

    /**
     * Load unique categories from API and populate select dropdown
     */
    async function loadCategories() {
        try {
            const res = await fetch(`${API_BASE}/categories`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const categories = await res.json();

            while (categoryFilter.options.length > 1) {
                categoryFilter.remove(1);
            }

            categories.forEach(cat => {
                const opt = document.createElement("option");
                opt.value = cat;
                opt.textContent = cat;
                categoryFilter.appendChild(opt);
            });
        } catch (err) {
            console.warn("Could not load categories dropdown:", err);
        }
    }

    /**
     * Fetch products from API using active filter params
     */
    async function fetchProducts() {
        setState("loading");

        const params = new URLSearchParams();
        const search = searchInput.value.trim();
        const category = categoryFilter.value.trim();
        const availability = availabilityFilter.value.trim();

        if (search) params.set("search", search);
        if (category) params.set("category", category);
        if (availability) params.set("available", availability);

        const url = `${API_BASE}?${params.toString()}`;

        try {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`Server returned HTTP ${res.status}`);
            const products = await res.json();

            while (productsGrid.firstChild) {
                productsGrid.removeChild(productsGrid.firstChild);
            }

            if (!Array.isArray(products) || products.length === 0) {
                setState("empty");
                return;
            }

            products.forEach(p => {
                productsGrid.appendChild(createProductCard(p));
            });

            setState("content");
        } catch (err) {
            setState("error", err.message || "Failed to fetch products.");
        }
    }

    /**
     * Setup Demo Spending Mandate
     */
    async function setupDemoMandate() {
        quickMandateBtn.disabled = true;
        quickMandateBtn.textContent = "Setting up mandate...";

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

            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            activeMandate = await res.json();

            mandateIdDisplay.textContent = activeMandate.id;
            mandateBudgetDisplay.textContent = formatINR(activeMandate.max_amount);
            mandateCategoriesDisplay.textContent = activeMandate.permitted_categories.join(", ");
            mandateStatusPill.textContent = "ACTIVE";
            mandateStatusPill.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-500/20 text-emerald-300 font-semibold";

            quickMandateBtn.textContent = "Mandate Active (₹5,000)";
            quickMandateBtn.className = "w-full px-3 py-2 bg-slate-800 text-slate-400 border border-slate-700 rounded-lg text-xs font-medium cursor-default";

            appendChatMessage("assistant", `Spending mandate established! Budget is ₹5,000 for Electronics & Books. Ask me for recommendations within your budget.`);
        } catch (err) {
            alert("Failed to create mandate: " + err.message);
            quickMandateBtn.disabled = false;
            quickMandateBtn.textContent = "Setup Demo Mandate (₹5,000)";
        }
    }

    /**
     * Append message to AI Chat Feed
     */
    function appendChatMessage(role, text) {
        const bubble = document.createElement("div");
        if (role === "user") {
            bubble.className = "p-3 rounded-xl bg-indigo-600/30 border border-indigo-500/40 text-indigo-100 ml-8 leading-relaxed";
        } else {
            bubble.className = "p-3 rounded-xl bg-slate-800/80 border border-slate-700/60 text-slate-200 mr-8 leading-relaxed";
        }
        bubble.textContent = text;
        aiChatFeed.appendChild(bubble);
        aiChatFeed.scrollTop = aiChatFeed.scrollHeight;
    }

    /**
     * Render Proposal Evaluation Card with Secure User Approval Button
     */
    function renderProposalCard(proposal, policyResult, approvalSecret) {
        const card = document.createElement("div");
        card.className = "p-4 rounded-xl bg-slate-950 border border-indigo-500/30 mr-8 my-2 shadow-lg";

        const titleRow = document.createElement("div");
        titleRow.className = "flex items-center justify-between mb-2";

        const title = document.createElement("h4");
        title.className = "text-xs font-bold uppercase tracking-wider text-indigo-400";
        title.textContent = "Recommended Purchase Proposal";

        const badge = document.createElement("span");
        if (policyResult.decision === "ALLOW_PENDING_APPROVAL") {
            badge.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-500/20 text-emerald-400 font-semibold border border-emerald-500/30";
            badge.textContent = "POLICY ALLOWED";
        } else {
            badge.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-red-500/20 text-red-400 font-semibold border border-red-500/30";
            badge.textContent = "BLOCKED BY POLICY";
        }

        titleRow.appendChild(title);
        titleRow.appendChild(badge);
        card.appendChild(titleRow);

        // Product details
        const info = document.createElement("p");
        info.className = "text-xs text-white font-medium mb-1";
        info.textContent = `${proposal.product_name || policyResult.product_id} (Qty: ${policyResult.quantity})`;
        card.appendChild(info);

        const priceRow = document.createElement("div");
        priceRow.className = "text-xs text-slate-400 mb-3";
        priceRow.textContent = `Authoritative Total: ${formatINR(policyResult.total_amount || 0)}`;
        card.appendChild(priceRow);

        // Policy explanation
        const reason = document.createElement("p");
        reason.className = "text-[11px] text-slate-400 italic mb-3";
        reason.textContent = policyResult.reason_message;
        card.appendChild(reason);

        // Action button if allowed
        if (policyResult.decision === "ALLOW_PENDING_APPROVAL" && approvalSecret) {
            let oneTimeSecret = approvalSecret;
            const approveBtn = document.createElement("button");
            approveBtn.type = "button";
            approveBtn.className = "w-full py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-semibold shadow transition";
            approveBtn.textContent = "Explicitly Approve Purchase (One-Time)";

            // Container for checkout lifecycle
            const checkoutContainer = document.createElement("div");
            checkoutContainer.className = "mt-3 pt-3 border-t border-slate-800 space-y-3 hidden";

            approveBtn.addEventListener("click", async () => {
                if (!oneTimeSecret) return;
                approveBtn.disabled = true;
                approveBtn.textContent = "Verifying cryptographic secret...";

                try {
                    const res = await fetch(`${MANDATE_API}/proposals/${proposal.id}/approve`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ approval_secret: oneTimeSecret }),
                    });

                    if (!res.ok) {
                        const errData = await res.json().catch(() => ({}));
                        throw new Error(errData.detail || `HTTP ${res.status}`);
                    }

                    // Zero out in-memory secret immediately
                    oneTimeSecret = null;

                    approveBtn.textContent = "✓ USER APPROVED (Mandate Reserved)";
                    approveBtn.className = "w-full py-2 bg-emerald-950/60 text-emerald-300 border border-emerald-500/40 rounded-lg text-xs font-semibold cursor-default";

                    // Update mandate status display to RESERVED
                    mandateStatusPill.textContent = "RESERVED (HELD)";
                    mandateStatusPill.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-amber-500/20 text-amber-300 font-semibold";

                    // Reveal checkout container
                    checkoutContainer.classList.remove("hidden");
                    renderCheckoutFlow(checkoutContainer, proposal.id);
                } catch (err) {
                    alert("Approval failed: " + err.message);
                    approveBtn.disabled = false;
                    approveBtn.textContent = "Retry Approval";
                }
            });

            card.appendChild(approveBtn);
            card.appendChild(checkoutContainer);
        }

        aiChatFeed.appendChild(card);
        aiChatFeed.scrollTop = aiChatFeed.scrollHeight;
    }

    /**
     * Render PayPal Sandbox Checkout Flow within an approved proposal card
     */
    function renderCheckoutFlow(container, proposalId) {
        while (container.firstChild) container.removeChild(container.firstChild);

        const initBtn = document.createElement("button");
        initBtn.type = "button";
        initBtn.className = "w-full py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold shadow transition flex items-center justify-center gap-2";
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
                renderOrderDetails(container, orderData, proposalId);
            } catch (err) {
                errorMsg.textContent = "Failed to create order: " + err.message;
                errorMsg.classList.remove("hidden");
                initBtn.disabled = false;
                initBtn.textContent = "Retry Creating Sandbox Order";
            }
        });

        container.appendChild(initBtn);
        container.appendChild(errorMsg);
    }

    /**
     * Render active PayPal Sandbox order breakdown and capture/cancel actions
     */
    function renderOrderDetails(container, orderData, proposalId) {
        while (container.firstChild) container.removeChild(container.firstChild);

        const breakdown = orderData.currency_breakdown;

        // Currency Breakdown Box
        const breakdownBox = document.createElement("div");
        breakdownBox.className = "p-3 rounded-lg bg-slate-900/90 border border-slate-700/80 space-y-2 text-xs";

        const headerRow = document.createElement("div");
        headerRow.className = "flex justify-between items-center pb-2 border-b border-slate-800";
        const hTitle = document.createElement("span");
        hTitle.className = "font-semibold text-slate-300";
        hTitle.textContent = "PayPal Sandbox Dual-Currency Checkout";
        const orderPill = document.createElement("span");
        orderPill.className = "px-1.5 py-0.5 rounded text-[10px] font-mono bg-indigo-500/20 text-indigo-300";
        orderPill.textContent = orderData.order_id;
        headerRow.appendChild(hTitle);
        headerRow.appendChild(orderPill);
        breakdownBox.appendChild(headerRow);

        // Grid for amounts
        const grid = document.createElement("div");
        grid.className = "space-y-1 font-mono text-[11px]";

        const inrRow = document.createElement("div");
        inrRow.className = "flex justify-between text-slate-400";
        const inrLabel = document.createElement("span");
        inrLabel.textContent = "Authoritative INR Total:";
        const inrVal = document.createElement("span");
        inrVal.className = "text-white font-semibold";
        inrVal.textContent = formatINR(breakdown.inr_total);
        inrRow.appendChild(inrLabel);
        inrRow.appendChild(inrVal);

        const usdRow = document.createElement("div");
        usdRow.className = "flex justify-between text-slate-400";
        const usdLabel = document.createElement("span");
        usdLabel.textContent = "PayPal Sandbox USD Total:";
        const usdVal = document.createElement("span");
        usdVal.className = "text-emerald-400 font-semibold";
        usdVal.textContent = `$${breakdown.usd_amount} USD`;
        usdRow.appendChild(usdLabel);
        usdRow.appendChild(usdVal);

        const rateRow = document.createElement("div");
        rateRow.className = "flex justify-between text-slate-500 text-[10px]";
        const rateLabel = document.createElement("span");
        rateLabel.textContent = "Configured Demo Rate:";
        const rateVal = document.createElement("span");
        rateVal.textContent = `1 INR = ${breakdown.exchange_rate} USD`;
        rateRow.appendChild(rateLabel);
        rateRow.appendChild(rateVal);

        grid.appendChild(inrRow);
        grid.appendChild(usdRow);
        grid.appendChild(rateRow);
        breakdownBox.appendChild(grid);

        // Test-only disclaimer
        const disc = document.createElement("p");
        disc.className = "text-[10px] text-amber-300/80 italic pt-1 border-t border-slate-800";
        disc.textContent = breakdown.disclaimer;
        breakdownBox.appendChild(disc);

        container.appendChild(breakdownBox);

        // Status Banner
        const statusBanner = document.createElement("div");
        statusBanner.className = "text-center text-xs py-1.5 px-2 rounded font-mono bg-slate-900 border border-slate-700 text-slate-300";
        statusBanner.textContent = `Order Status: ${orderData.status}`;
        container.appendChild(statusBanner);

        // Approval Link (Opens PayPal Sandbox in new window)
        const payLink = document.createElement("a");
        payLink.href = orderData.approve_url;
        payLink.target = "_blank";
        payLink.rel = "noopener noreferrer";
        payLink.className = "block text-center py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold transition";
        payLink.textContent = "Open PayPal Sandbox Approval Window ↗";
        container.appendChild(payLink);

        // Action Buttons Row (Capture & Cancel)
        const actionRow = document.createElement("div");
        actionRow.className = "grid grid-cols-2 gap-2";

        const captureBtn = document.createElement("button");
        captureBtn.type = "button";
        captureBtn.className = "py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-semibold transition disabled:opacity-50";
        captureBtn.textContent = "Verify & Capture Payment";

        const cancelBtn = document.createElement("button");
        cancelBtn.type = "button";
        cancelBtn.className = "py-2 bg-red-600/30 hover:bg-red-600/50 text-red-200 border border-red-500/40 rounded-lg text-xs font-medium transition disabled:opacity-50";
        cancelBtn.textContent = "Cancel Checkout";

        actionRow.appendChild(captureBtn);
        actionRow.appendChild(cancelBtn);
        container.appendChild(actionRow);

        const resultNotice = document.createElement("div");
        resultNotice.className = "text-xs hidden p-2 rounded";
        container.appendChild(resultNotice);

        // Capture Action
        captureBtn.addEventListener("click", async () => {
            captureBtn.disabled = true;
            cancelBtn.disabled = true;
            captureBtn.textContent = "Verifying with PayPal...";

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
                    statusBanner.textContent = `✓ PAID: Verified Capture ${capResult.capture_id}`;

                    // Update mandate pill to CONSUMED
                    mandateStatusPill.textContent = "CONSUMED";
                    mandateStatusPill.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-purple-500/20 text-purple-300 font-semibold";

                    payLink.remove();
                    actionRow.remove();

                    appendChatMessage("assistant", `🎉 Payment verified and completed! Capture ID: ${capResult.capture_id}. The mandate is now CONSUMED.`);
                } else {
                    throw new Error(`Unexpected capture status: ${capResult.status}`);
                }
            } catch (err) {
                resultNotice.className = "text-xs p-2 rounded bg-red-500/20 text-red-300 border border-red-500/30 block";
                resultNotice.textContent = "Capture failed: " + err.message;
                captureBtn.disabled = false;
                cancelBtn.disabled = false;
                captureBtn.textContent = "Retry Capture Verification";
            }
        });

        // Cancel Action
        cancelBtn.addEventListener("click", async () => {
            cancelBtn.disabled = true;
            captureBtn.disabled = true;
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
                mandateStatusPill.textContent = "ACTIVE";
                mandateStatusPill.className = "px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-500/20 text-emerald-300 font-semibold";

                payLink.remove();
                actionRow.remove();

                appendChatMessage("assistant", `Checkout was cancelled. The mandate reservation has been safely released back to ACTIVE.`);
            } catch (err) {
                resultNotice.className = "text-xs p-2 rounded bg-red-500/20 text-red-300 border border-red-500/30 block";
                resultNotice.textContent = "Cancellation failed: " + err.message;
                cancelBtn.disabled = false;
                captureBtn.disabled = false;
                cancelBtn.textContent = "Cancel Checkout";
            }
        });
    }

    /**
     * Send AI Chat Message
     */
    async function handleAIChatSubmit(e) {
        e.preventDefault();
        const text = aiChatInput.value.trim();
        if (!text) return;

        const lang = aiLanguageSelect.value;
        appendChatMessage("user", text);
        aiChatInput.value = "";
        aiSendBtn.disabled = true;
        aiSendBtn.textContent = "...";

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

            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();

            appendChatMessage("assistant", data.reply_text);
            conversationHistory.push({ role: "assistant", content: data.reply_text });

            // If a proposal with policy result exists, render the proposal card
            if (data.evaluated_policy_result && data.evaluated_policy_result.proposal) {
                renderProposalCard(
                    data.evaluated_policy_result.proposal,
                    data.evaluated_policy_result,
                    data.evaluated_policy_result.approval_secret
                );
            } else if (data.evaluated_policy_result && data.evaluated_policy_result.decision === "BLOCKED") {
                renderProposalCard(
                    { product_name: data.evaluated_policy_result.product_id },
                    data.evaluated_policy_result,
                    null
                );
            }
        } catch (err) {
            appendChatMessage("assistant", "Sorry, an error occurred while connecting to the assistant: " + err.message);
        } finally {
            aiSendBtn.disabled = false;
            aiSendBtn.textContent = "Send";
        }
    }

    // Event Listeners
    searchInput.addEventListener("input", () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(fetchProducts, 300);
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

    // Initial catalogue load
    loadCategories();
    fetchProducts();
});
