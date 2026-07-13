/* ai_assistant — page script (extracted from inline <script>). */
function aiAssistantData() {
    return {
        loading: true,
        activeTab: 'all',
        searchQuery: '',
        sortBy: 'severity',
        selectedProblem: null,
        selectedArea: null,
        
        // Chat with Claude
        showChat: false,
        chatMessages: [],
        chatInput: '',
        chatLoading: false,
        
        // Areas selection
        showAreasPanel: false,
        availableAreas: [],
        selectedAreas: [],
        areaSearchQuery: '',
        savingAreas: false,
        
        // Groups selection
        showGroupsPanel: false,
        availableGroups: [],
        selectedGroups: [],
        groupSearchQuery: '',
        savingGroups: false,
        
        // Settings panel
        showSettingsPanel: false,
        savingSettings: false,
        settings: {
            // Высокий долг
            minDebt: 100000,
            debtRatioInfo: 30,
            debtRatioWarning: 50,
            debtRatioCritical: 100,
            // Низкие платежи
            minSalesForPayment: 100000,
            paymentRateInfo: 50,
            paymentRateWarning: 35,
            paymentRateCritical: 20,
            // Падение продаж
            minAvgSales: 100000,
            salesDropInfo: 30,
            salesDropWarning: 50,
            salesDropCritical: 70,
            // Неактивные клиенты
            minAvgSalesInactive: 50000,
            inactiveDaysInfo: 30,
            inactiveDaysWarning: 45,
            inactiveDaysCritical: 60
        },
        defaultSettings: null,
        
        stats: {
            critical: 0,
            warnings: 0,
            attention: 0,
            totalCustomers: 0
        },
        
        problems: {
            highDebt: [],
            lowPayment: [],
            salesDrop: [],
            inactive: [],
            irregular: []
        },
        
        areaStats: [],
        
        async init() {
            this.defaultSettings = JSON.parse(JSON.stringify(this.settings));
            await this.loadSettings();
            await this.loadAreas();
            await this.loadSelectedAreas();
            await this.loadGroups();
            await this.loadSelectedGroups();
            await this.loadAnalysis();
        },
        
        // Settings methods
        async loadSettings() {
            try {
                const response = await fetch('/api/ai-assistant/settings');
                const data = await response.json();
                if (data.success && data.data) {
                    this.settings = { ...this.settings, ...data.data };
                }
            } catch (error) {
                console.error('Error loading settings:', error);
            }
        },
        
        async saveSettings() {
            this.savingSettings = true;
            try {
                const response = await fetch('/api/ai-assistant/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ settings: this.settings })
                });
                const data = await response.json();
                if (data.success) {
                    this.showSettingsPanel = false;
                    await this.loadAnalysis();
                } else {
                    alert('Ошибка сохранения: ' + data.error);
                }
            } catch (error) {
                console.error('Error saving settings:', error);
                alert('Ошибка сохранения');
            }
            this.savingSettings = false;
        },
        
        resetSettings() {
            this.settings = JSON.parse(JSON.stringify(this.defaultSettings));
        },
        
        // Areas (Territories) methods
        async loadAreas() {
            try {
                const response = await fetch('/api/sales-areas-list');
                const data = await response.json();
                if (data.success) {
                    this.availableAreas = data.data;
                }
            } catch (error) {
                console.error('Error loading areas:', error);
            }
        },
        
        async loadSelectedAreas() {
            try {
                const response = await fetch('/api/ai-assistant/areas');
                const data = await response.json();
                if (data.success) {
                    this.selectedAreas = data.data || [];
                }
            } catch (error) {
                console.error('Error loading selected areas:', error);
            }
        },
        
        get filteredAvailableAreas() {
            if (!this.areaSearchQuery) return this.availableAreas;
            const query = this.areaSearchQuery.toLowerCase();
            return this.availableAreas.filter(a => 
                a.code.toLowerCase().includes(query) || 
                a.name.toLowerCase().includes(query)
            );
        },
        
        toggleArea(code) {
            const index = this.selectedAreas.indexOf(code);
            if (index === -1) {
                this.selectedAreas.push(code);
            } else {
                this.selectedAreas.splice(index, 1);
            }
        },
        
        selectAllAreas() {
            this.selectedAreas = this.availableAreas.map(a => a.code);
        },
        
        clearAllAreas() {
            this.selectedAreas = [];
        },
        
        async saveAreas() {
            this.savingAreas = true;
            try {
                const response = await fetch('/api/ai-assistant/areas', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ areas: this.selectedAreas })
                });
                const data = await response.json();
                if (data.success) {
                    this.showAreasPanel = false;
                    await this.loadAnalysis();
                } else {
                    alert('Ошибка сохранения: ' + data.error);
                }
            } catch (error) {
                console.error('Error saving areas:', error);
                alert('Ошибка сохранения');
            }
            this.savingAreas = false;
        },
        
        // Groups methods
        async loadGroups() {
            try {
                const response = await fetch('/api/customer-groups');
                const data = await response.json();
                if (data.success) {
                    this.availableGroups = data.data;
                }
            } catch (error) {
                console.error('Error loading groups:', error);
            }
        },
        
        async loadSelectedGroups() {
            try {
                const response = await fetch('/api/ai-assistant/groups');
                const data = await response.json();
                if (data.success) {
                    this.selectedGroups = data.data || [];
                }
            } catch (error) {
                console.error('Error loading selected groups:', error);
            }
        },
        
        get filteredAvailableGroups() {
            if (!this.groupSearchQuery) return this.availableGroups;
            const query = this.groupSearchQuery.toLowerCase();
            return this.availableGroups.filter(g => 
                g.code.toLowerCase().includes(query) || 
                g.name.toLowerCase().includes(query)
            );
        },
        
        toggleGroup(code) {
            const index = this.selectedGroups.indexOf(code);
            if (index === -1) {
                this.selectedGroups.push(code);
            } else {
                this.selectedGroups.splice(index, 1);
            }
        },
        
        selectAllGroups() {
            this.selectedGroups = this.availableGroups.map(g => g.code);
        },
        
        clearAllGroups() {
            this.selectedGroups = [];
        },
        
        async saveGroups() {
            this.savingGroups = true;
            try {
                const response = await fetch('/api/ai-assistant/groups', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ groups: this.selectedGroups })
                });
                const data = await response.json();
                if (data.success) {
                    this.showGroupsPanel = false;
                    await this.loadAnalysis();
                } else {
                    alert('Ошибка сохранения: ' + data.error);
                }
            } catch (error) {
                console.error('Error saving groups:', error);
                alert('Ошибка сохранения');
            }
            this.savingGroups = false;
        },
        
        async loadAnalysis() {
            this.loading = true;
            try {
                // Передаем параметры в API
                const params = new URLSearchParams(this.settings);
                const response = await fetch('/api/ai-analysis?' + params.toString());
                const data = await response.json();
                
                if (data.success) {
                    this.problems = data.problems;
                    this.stats = data.stats;
                    this.areaStats = data.areaStats;
                }
            } catch (error) {
                console.error('Error loading analysis:', error);
            }
            this.loading = false;
        },
        
        async refreshAnalysis() {
            await this.loadAnalysis();
        },
        
        // Claude Chat Methods
        async sendMessage() {
            if (!this.chatInput.trim() || this.chatLoading) return;
            
            const userMessage = this.chatInput.trim();
            this.chatInput = '';
            
            // Add user message to chat
            this.chatMessages.push({
                role: 'user',
                content: userMessage
            });
            
            this.chatLoading = true;
            this.scrollChatToBottom();
            
            try {
                // Build context with current data
                const context = {
                    stats: this.stats,
                    areaStats: this.areaStats,
                    topProblems: this.allProblems.slice(0, 10).map(p => ({
                        customer: p.customerName,
                        code: p.customerCode,
                        area: p.areaName,
                        problem: p.problemType,
                        description: p.description,
                        amount: p.amount,
                        severity: p.severity
                    }))
                };
                
                const response = await fetch('/api/ai-chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        message: userMessage,
                        context: context
                    })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    this.chatMessages.push({
                        role: 'assistant',
                        content: data.response
                    });
                } else {
                    this.chatMessages.push({
                        role: 'assistant',
                        content: `❌ Ошибка: ${data.error}`
                    });
                }
            } catch (error) {
                console.error('Chat error:', error);
                this.chatMessages.push({
                    role: 'assistant',
                    content: `❌ Ошибка подключения к Claude AI: ${error.message}`
                });
            }
            
            this.chatLoading = false;
            this.scrollChatToBottom();
        },
        
        sendQuickQuestion(question) {
            this.chatInput = question;
            this.sendMessage();
        },
        
        clearChat() {
            this.chatMessages = [];
        },
        
        scrollChatToBottom() {
            this.$nextTick(() => {
                if (this.$refs.chatContainer) {
                    this.$refs.chatContainer.scrollTop = this.$refs.chatContainer.scrollHeight;
                }
            });
        },
        
        formatMessage(text) {
            // Convert markdown-like formatting to HTML
            return text
                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                .replace(/\*(.*?)\*/g, '<em>$1</em>')
                .replace(/`(.*?)`/g, '<code>$1</code>')
                .replace(/^### (.*$)/gm, '<h6 class="mt-2 mb-1">$1</h6>')
                .replace(/^## (.*$)/gm, '<h5 class="mt-2 mb-1">$1</h5>')
                .replace(/^# (.*$)/gm, '<h4 class="mt-2 mb-1">$1</h4>')
                .replace(/^- (.*$)/gm, '• $1')
                .replace(/^\d+\. (.*$)/gm, '<span class="text-info">$&</span>');
        },
        
        // AI Analysis for selected customer
        async analyzeCustomerWithAI(customer) {
            if (!customer) return;
            
            this.chatLoading = true;
            this.showChat = true;
            
            try {
                const response = await fetch('/api/ai-analyze-customer', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ customer: customer })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    this.chatMessages.push({
                        role: 'user',
                        content: `Проанализируй клиента: ${customer.customerName} (${customer.customerCode})`
                    });
                    this.chatMessages.push({
                        role: 'assistant',
                        content: data.analysis
                    });
                } else {
                    this.chatMessages.push({
                        role: 'assistant',
                        content: `❌ Ошибка: ${data.error}`
                    });
                }
            } catch (error) {
                console.error('Analysis error:', error);
                this.chatMessages.push({
                    role: 'assistant',
                    content: `❌ Ошибка подключения к Claude AI: ${error.message}`
                });
            }
            
            this.chatLoading = false;
            this.scrollChatToBottom();
        },
        
        get allProblems() {
            const all = [
                ...(this.problems.highDebt || []),
                ...(this.problems.lowPayment || []),
                ...(this.problems.salesDrop || []),
                ...(this.problems.inactive || []),
                ...(this.problems.irregular || [])
            ];
            return all;
        },
        
        get filteredProblems() {
            let list = [];
            
            switch(this.activeTab) {
                case 'all':
                    list = this.allProblems;
                    break;
                case 'debt':
                    list = this.problems.highDebt || [];
                    break;
                case 'payment':
                    list = this.problems.lowPayment || [];
                    break;
                case 'sales':
                    list = this.problems.salesDrop || [];
                    break;
                case 'inactive':
                    list = this.problems.inactive || [];
                    break;
                case 'irregular':
                    list = this.problems.irregular || [];
                    break;
            }
            
            // Filter by search
            if (this.searchQuery) {
                const query = this.searchQuery.toLowerCase();
                list = list.filter(p => 
                    p.customerName.toLowerCase().includes(query) ||
                    p.customerCode.toString().includes(query) ||
                    p.areaCode.toLowerCase().includes(query)
                );
            }
            
            // Filter by area
            if (this.selectedArea) {
                list = list.filter(p => p.areaCode === this.selectedArea);
            }
            
            // Sort
            list.sort((a, b) => {
                switch(this.sortBy) {
                    case 'severity':
                        const severityOrder = { critical: 0, warning: 1, info: 2 };
                        return severityOrder[a.severity] - severityOrder[b.severity];
                    case 'amount':
                        return Math.abs(b.amount) - Math.abs(a.amount);
                    case 'area':
                        return a.areaCode.localeCompare(b.areaCode);
                    default:
                        return 0;
                }
            });
            
            return list;
        },
        
        getTabTitle() {
            const titles = {
                'all': 'Все проблемы',
                'debt': 'Высокий долг (>30% от продаж)',
                'payment': 'Низкий уровень платежей (<50%)',
                'sales': 'Падение продаж (>30% от среднего)',
                'inactive': 'Неактивные клиенты (>30 дней)',
                'irregular': 'Нерегулярные заказы'
            };
            return titles[this.activeTab] || 'Проблемы';
        },
        
        selectProblem(problem) {
            this.selectedProblem = problem;
        },
        
        showDetails(problem) {
            this.selectedProblem = problem;
            // Could open a modal here if needed
        },
        
        filterByArea(areaCode) {
            if (this.selectedArea === areaCode) {
                this.selectedArea = null;
            } else {
                this.selectedArea = areaCode;
            }
        },
        
        formatNumber(num) {
            if (num === null || num === undefined) return '0';
            return Math.round(num).toLocaleString('ru-RU');
        }
    };
}
