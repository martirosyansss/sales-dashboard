/* settings — page script (extracted from inline <script>). */
    function settingsData() {
        return {
            managers: [],
            stores: [],
            filteredStores: [],
            groups: [],
            groupsWithStats: [],
            distributors: [],
            selectedManager: null,
            managerStores: [],
            availableStores: [],
            storeSearch: '',
            newStoreId: '',
            newGroupName: '',
            excludedCustomers: [],
            filteredExcludedCustomers: [],
            excludedSearch: '',
            excludeCustomerSearch: '',
            customersToExclude: [],
            selectedCustomerToExclude: '',
            excludeReason: 'Неблагонадежный',
            excludedGroups: [],
            groupManagerAssignments: {},
            selectedManagerForGroups: null,  // Новое: выбранный менеджер для назначения групп
            bulkEditMode: false,  // Режим массового изменения
            selectedManagersForBulk: [],  // Выбранные менеджеры для массового изменения
            selectedGroupsForBulk: [],  // Выбранные группы для массового изменения
            productGroups: [],  // Список всех групп товаров
            selectedProductGroups: [],  // Выбранные группы товаров для фильтрации
            salesAreas: [],  // Территории продаж
            salesAreaGroupAssignments: {},  // Назначения групп для Sales Areas
            selectedSalesArea: null,
            selectedSalesAreaGroups: [],

            // ----- Пользователи (учётные записи панели) -----
            users: [],
            userForm: { editing: false, username: '', display_name: '', role: 'user', areas: [], password: '' },

            async init() {
                // Загружаем только критически важные данные параллельно
                await Promise.all([
                    this.loadManagers(),
                    this.loadStores(),
                    this.loadDistributors(),
                    this.loadExcludedCustomers(),
                    this.loadGroupsWithStats(),
                    this.loadProductGroups(),
                    this.loadSelectedProductGroups(),
                    this.loadSalesAreas(),
                    this.loadSalesAreaGroupAssignments(),
                    this.loadUsers()
                ]);
                // loadGroups() убран - он нужен только если есть dropdown для групп
            },

            // Показать toast-уведомление
            showSuccess(message) {
                const toastEl = document.getElementById('successToast');
                const messageEl = document.getElementById('successToastMessage');
                messageEl.textContent = message;
                const toast = new bootstrap.Toast(toastEl, { delay: 3000 });
                toast.show();
            },

            async loadManagers() {
                try {
                    const response = await fetch('/api/settings/managers');
                    const result = await response.json();
                    if (result.success) {
                        this.managers = result.data;
                    }
                } catch (error) {
                    console.error('Ошибка загрузки менеджеров:', error);
                }
            },

            async loadStores() {
                try {
                    const response = await fetch('/api/settings/stores');
                    const result = await response.json();
                    if (result.success) {
                        this.stores = result.data;
                        this.filteredStores = result.data;
                    }
                } catch (error) {
                    console.error('Ошибка загрузки магазинов:', error);
                }
            },

            async loadGroups() {
                try {
                    const response = await fetch('/api/settings/groups');
                    const result = await response.json();
                    if (result.success) {
                        this.groups = result.data;
                    }
                } catch (error) {
                    console.error('Ошибка загрузки групп:', error);
                }
            },

            async loadGroupsWithStats() {
                try {
                    console.log('🔄 Загрузка групп со статистикой...');
                    
                    // Загрузить все данные параллельно
                    const [groupsResp, excludedResp, assignmentsResp] = await Promise.all([
                        fetch('/api/settings/groups-with-stats'),
                        fetch('/api/settings/excluded-groups'),
                        fetch('/api/settings/group-manager-assignments')
                    ]);
                    
                    const groupsResult = await groupsResp.json();
                    const excludedResult = await excludedResp.json();
                    const assignmentsResult = await assignmentsResp.json();
                    
                    console.log('📦 Получены данные:', {
                        groups: groupsResult.data?.length,
                        excluded: excludedResult.data?.length,
                        assignments: Object.keys(assignmentsResult.data || {}).length
                    });
                    
                    if (groupsResult.success) {
                        // Сохранить справочные данные
                        this.excludedGroups = excludedResult.success ? excludedResult.data : [];
                        this.groupManagerAssignments = assignmentsResult.success ? assignmentsResult.data : {};
                        
                        // Обновить группы с учетом исключений и назначений ЗА ОДИН РАЗ
                        const excludedSet = new Set(this.excludedGroups);
                        this.groupsWithStats = groupsResult.data.map(g => ({
                            ...g,
                            isExcluded: excludedSet.has(g.fGROUP),
                            assignedManager: this.groupManagerAssignments[g.fGROUP] || ''
                        }));
                        
                        console.log('✅ Загружено групп:', this.groupsWithStats.length);
                    } else {
                        console.error('❌ Ошибка от сервера:', groupsResult.error);
                    }
                } catch (error) {
                    console.error('❌ Ошибка загрузки групп со статистикой:', error);
                }
            },

            async loadExcludedGroups() {
                try {
                    const response = await fetch('/api/settings/excluded-groups');
                    const result = await response.json();
                    if (result.success) {
                        this.excludedGroups = result.data;
                    }
                } catch (error) {
                    console.error('Ошибка загрузки исключенных групп:', error);
                }
            },

            async loadGroupManagerAssignments() {
                try {
                    const response = await fetch('/api/settings/group-manager-assignments');
                    const result = await response.json();
                    if (result.success) {
                        this.groupManagerAssignments = result.data;
                    }
                } catch (error) {
                    console.error('Ошибка загрузки назначений менеджеров:', error);
                }
            },

            async loadDistributors() {
                try {
                    const response = await fetch('/api/settings/distributors');
                    const result = await response.json();
                    if (result.success) {
                        this.distributors = result.data;
                    }
                } catch (error) {
                    console.error('Ошибка загрузки дистрибьюторов:', error);
                }
            },

            async loadSalesAreas() {
                try {
                    const response = await fetch('/api/settings/sales-areas/list');
                    const result = await response.json();
                    if (result.success) {
                        this.salesAreas = result.data;
                    }
                } catch (error) {
                    console.error('Ошибка загрузки Sales Areas:', error);
                }
            },

            async loadSalesAreaGroupAssignments() {
                try {
                    const response = await fetch('/api/settings/sales-areas/groups');
                    const result = await response.json();
                    if (result.success) {
                        this.salesAreaGroupAssignments = result.data || {};
                        if (this.selectedSalesArea) {
                            const code = this.selectedSalesArea.code;
                            this.selectedSalesAreaGroups = [...(this.salesAreaGroupAssignments[code] || [])];
                        }
                    }
                } catch (error) {
                    console.error('Ошибка загрузки назначений Sales Areas:', error);
                }
            },

            selectSalesArea(area) {
                this.selectedSalesArea = area;
                this.selectedSalesAreaGroups = [...(this.salesAreaGroupAssignments[area.code] || [])];
            },

            getSalesAreaGroupCount(areaCode) {
                const groups = this.salesAreaGroupAssignments[areaCode];
                return groups ? groups.length : 0;
            },

            isGroupAssignedToSelectedSalesArea(groupCode) {
                if (!this.selectedSalesArea) return false;
                return this.selectedSalesAreaGroups.includes(groupCode);
            },

            async toggleGroupForSalesArea(groupCode, isChecked) {
                if (!this.selectedSalesArea) return;
                if (isChecked) {
                    if (!this.selectedSalesAreaGroups.includes(groupCode)) {
                        this.selectedSalesAreaGroups.push(groupCode);
                    }
                } else {
                    this.selectedSalesAreaGroups = this.selectedSalesAreaGroups.filter(g => g !== groupCode);
                }
                await this.persistSalesAreaGroups();
            },

            async selectAllGroupsForSalesArea() {
                if (!this.selectedSalesArea) return;
                this.selectedSalesAreaGroups = this.groupsWithStats.map(g => g.fGROUP);
                await this.persistSalesAreaGroups();
            },

            async clearSalesAreaGroups() {
                if (!this.selectedSalesArea) return;
                this.selectedSalesAreaGroups = [];
                await this.persistSalesAreaGroups();
            },

            async persistSalesAreaGroups() {
                if (!this.selectedSalesArea) return;
                try {
                    const response = await fetch('/api/settings/sales-areas/groups/set', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            areaCode: this.selectedSalesArea.code,
                            groups: this.selectedSalesAreaGroups
                        })
                    });
                    const result = await response.json();
                    if (result.success) {
                        this.salesAreaGroupAssignments[this.selectedSalesArea.code] = [...this.selectedSalesAreaGroups];
                        this.showSuccess(`Сохранены группы для территории ${this.selectedSalesArea.code}`);
                    } else {
                        alert(result.error || 'Ошибка сохранения групп Territory');
                    }
                } catch (error) {
                    console.error('Ошибка сохранения групп Territory:', error);
                }
            },

            async selectManager(manager) {
                this.selectedManager = manager;
                await this.loadManagerStores(manager.fID);
            },

            async loadManagerStores(managerId) {
                try {
                    const response = await fetch(`/api/settings/managers/${managerId}/stores`);
                    const result = await response.json();
                    if (result.success) {
                        this.managerStores = result.data;
                    }
                } catch (error) {
                    console.error('Ошибка загрузки магазинов менеджера:', error);
                }
            },

            showAddStoreModal() {
                this.availableStores = this.stores.filter(s => 
                    !this.managerStores.find(ms => ms.fID === s.fID)
                );
                const modal = new bootstrap.Modal(document.getElementById('addStoreModal'));
                modal.show();
            },

            async addStoreToManager() {
                if (!this.newStoreId || !this.selectedManager) return;

                try {
                    const response = await fetch('/api/settings/managers/assign-store', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            managerId: this.selectedManager.fID,
                            storeId: this.newStoreId
                        })
                    });

                    const result = await response.json();
                    if (result.success) {
                        await this.loadManagerStores(this.selectedManager.fID);
                        bootstrap.Modal.getInstance(document.getElementById('addStoreModal')).hide();
                        this.newStoreId = '';
                    }
                } catch (error) {
                    console.error('Ошибка добавления магазина:', error);
                }
            },

            async removeStoreFromManager(storeId) {
                if (!confirm('Удалить связь с этим магазином?')) return;

                try {
                    const response = await fetch('/api/settings/managers/unassign-store', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            managerId: this.selectedManager.fID,
                            storeId: storeId
                        })
                    });

                    const result = await response.json();
                    if (result.success) {
                        await this.loadManagerStores(this.selectedManager.fID);
                    }
                } catch (error) {
                    console.error('Ошибка удаления магазина:', error);
                }
            },

            filterStores() {
                const query = this.storeSearch.toLowerCase();
                this.filteredStores = this.stores.filter(s => 
                    s.fNAME.toLowerCase().includes(query) || 
                    s.fCODE.toLowerCase().includes(query)
                );
            },

            async updateStoreGroup(storeId, group) {
                try {
                    const response = await fetch('/api/settings/stores/update-group', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ storeId, group })
                    });

                    const result = await response.json();
                    if (result.success) {
                        await this.loadStores();
                    }
                } catch (error) {
                    console.error('Ошибка обновления группы:', error);
                }
            },

            showAddGroupModal() {
                const modal = new bootstrap.Modal(document.getElementById('addGroupModal'));
                modal.show();
            },

            async addGroup() {
                if (!this.newGroupName.trim()) return;

                try {
                    const response = await fetch('/api/settings/groups/add', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name: this.newGroupName })
                    });

                    const result = await response.json();
                    if (result.success) {
                        await this.loadGroups();
                        bootstrap.Modal.getInstance(document.getElementById('addGroupModal')).hide();
                        this.newGroupName = '';
                    }
                } catch (error) {
                    console.error('Ошибка добавления группы:', error);
                }
            },

            async deleteGroup(group) {
                if (!confirm(`Удалить группу "${group}"?`)) return;

                try {
                    const response = await fetch('/api/settings/groups/delete', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name: group })
                    });

                    const result = await response.json();
                    if (result.success) {
                        await this.loadGroups();
                        await this.loadStores();
                    }
                } catch (error) {
                    console.error('Ошибка удаления группы:', error);
                }
            },

            getStoresInGroup(group) {
                return this.stores.filter(s => s.fGROUP === group).length;
            },

            async assignDistributorToManager(distributorGroup, managerId) {
                try {
                    const response = await fetch('/api/settings/distributors/assign', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ distributorGroup, managerId })
                    });

                    const result = await response.json();
                    if (result.success) {
                        await this.loadDistributors();
                    }
                } catch (error) {
                    console.error('Ошибка назначения дистрибьютора:', error);
                }
            },

            editStore(store) {
                alert('Функция редактирования будет добавлена позже');
            },

            // ===== Исключенные клиенты =====
            async loadExcludedCustomers() {
                try {
                    const response = await fetch('/api/settings/excluded-customers');
                    const result = await response.json();
                    if (result.success) {
                        this.excludedCustomers = result.data;
                        this.filteredExcludedCustomers = result.data;
                    }
                } catch (error) {
                    console.error('Ошибка загрузки исключенных клиентов:', error);
                }
            },

            filterExcludedCustomers() {
                const query = this.excludedSearch.toLowerCase();
                this.filteredExcludedCustomers = this.excludedCustomers.filter(c => 
                    c.fNAME.toLowerCase().includes(query) || 
                    c.fCODE.toLowerCase().includes(query)
                );
            },

            showAddExcludedModal() {
                this.excludeCustomerSearch = '';
                this.customersToExclude = [];
                this.selectedCustomerToExclude = '';
                this.excludeReason = 'Неблагонадежный';
                const modal = new bootstrap.Modal(document.getElementById('addExcludedModal'));
                modal.show();
            },

            async searchCustomersToExclude() {
                if (this.excludeCustomerSearch.length < 2) {
                    this.customersToExclude = [];
                    return;
                }

                try {
                    const response = await fetch(`/api/settings/search-customers?query=${encodeURIComponent(this.excludeCustomerSearch)}`);
                    const result = await response.json();
                    if (result.success) {
                        // Фильтруем уже исключенных
                        this.customersToExclude = result.data.filter(c => 
                            !this.excludedCustomers.find(exc => exc.fID === c.fID)
                        );
                    }
                } catch (error) {
                    console.error('Ошибка поиска клиентов:', error);
                }
            },

            async addToExcluded() {
                if (!this.selectedCustomerToExclude) {
                    alert('Выберите клиента из списка');
                    return;
                }

                try {
                    const response = await fetch('/api/settings/excluded-customers/add', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            customerId: this.selectedCustomerToExclude,
                            reason: this.excludeReason
                        })
                    });

                    const result = await response.json();
                    if (result.success) {
                        await this.loadExcludedCustomers();
                        bootstrap.Modal.getInstance(document.getElementById('addExcludedModal')).hide();
                        alert('Клиент добавлен в список исключенных');
                    }
                } catch (error) {
                    console.error('Ошибка добавления исключенного:', error);
                }
            },

            async removeFromExcluded(customerId) {
                if (!confirm('Восстановить этого клиента? Он снова будет учитываться в статистике.')) {
                    return;
                }

                try {
                    const response = await fetch('/api/settings/excluded-customers/remove', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ customerId })
                    });

                    const result = await response.json();
                    if (result.success) {
                        await this.loadExcludedCustomers();
                        alert('Клиент восстановлен');
                    }
                } catch (error) {
                    console.error('Ошибка восстановления клиента:', error);
                }
            },

            getTotalExcludedDebt() {
                return this.excludedCustomers.reduce((sum, c) => sum + (c.debt || 0), 0);
            },

            getTotalExcludedSales() {
                return this.excludedCustomers.reduce((sum, c) => sum + (c.sales || 0), 0);
            },

            // ===== Исключение и восстановление групп =====
            async excludeGroup(groupCode) {
                const group = this.groupsWithStats.find(g => g.fGROUP === groupCode);
                if (!group) return;

                if (!confirm(`ИСКЛЮЧИТЬ группу "${groupCode}"?\n\nВсе ${group.customerCount} клиентов из этой группы будут исключены из расчетов!`)) {
                    return;
                }

                try {
                    const response = await fetch('/api/settings/excluded-groups/add', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ groupCode })
                    });

                    const result = await response.json();
                    if (result.success) {
                        await this.loadExcludedGroups();
                        await this.loadGroupsWithStats();
                        alert(`Группа "${groupCode}" исключена. ${group.customerCount} клиентов не будут учитываться в статистике.`);
                    }
                } catch (error) {
                    console.error('Ошибка исключения группы:', error);
                }
            },

            async restoreGroup(groupCode) {
                if (!confirm(`Восстановить группу "${groupCode}"?`)) return;

                try {
                    const response = await fetch('/api/settings/excluded-groups/remove', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ groupCode })
                    });

                    const result = await response.json();
                    if (result.success) {
                        await this.loadExcludedGroups();
                        await this.loadGroupsWithStats();
                        alert(`Группа "${groupCode}" восстановлена`);
                    }
                } catch (error) {
                    console.error('Ошибка восстановления группы:', error);
                }
            },

            async assignManagerToGroup(groupCode, managerId) {
                try {
                    const response = await fetch('/api/settings/group-manager-assignments/set', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ groupCode, managerId })
                    });

                    const result = await response.json();
                    if (result.success) {
                        await this.loadGroupManagerAssignments();
                        await this.loadGroupsWithStats();
                    }
                } catch (error) {
                    console.error('Ошибка назначения менеджера группе:', error);
                }
            },

            // Новые методы для работы с назначением групп менеджеру
            selectManagerForGroups(manager) {
                this.selectedManagerForGroups = manager;
            },

            getManagerGroupsCount(managerId) {
                return Object.entries(this.groupManagerAssignments)
                    .filter(([group, managers]) => {
                        // Поддержка старого формата (int) и нового (array)
                        if (Array.isArray(managers)) {
                            return managers.includes(managerId);
                        }
                        return managers === managerId;
                    })
                    .length;
            },

            isGroupAssignedToSelectedManager(groupCode) {
                if (!this.selectedManagerForGroups) return false;
                const managers = this.groupManagerAssignments[groupCode];
                if (!managers) return false;
                // Поддержка старого формата (int) и нового (array)
                if (Array.isArray(managers)) {
                    return managers.includes(this.selectedManagerForGroups.fID);
                }
                return managers === this.selectedManagerForGroups.fID;
            },

            async toggleGroupForManager(groupCode, isChecked, showToast = true) {
                if (!this.selectedManagerForGroups) return;
                
                const managerId = this.selectedManagerForGroups.fID;
                
                try {
                    let response;
                    if (isChecked) {
                        // Добавить менеджера к группе
                        response = await fetch('/api/settings/group-manager-assignments/set', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ groupCode, managerId })
                        });
                    } else {
                        // Удалить менеджера из группы
                        response = await fetch('/api/settings/group-manager-assignments/remove', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ groupCode, managerId })
                        });
                    }

                    const result = await response.json();
                    if (result.success) {
                        // Обновить локальные данные
                        if (isChecked) {
                            if (!this.groupManagerAssignments[groupCode]) {
                                this.groupManagerAssignments[groupCode] = [];
                            }
                            if (!Array.isArray(this.groupManagerAssignments[groupCode])) {
                                this.groupManagerAssignments[groupCode] = [this.groupManagerAssignments[groupCode]];
                            }
                            if (!this.groupManagerAssignments[groupCode].includes(managerId)) {
                                this.groupManagerAssignments[groupCode].push(managerId);
                            }
                            if (showToast) {
                                this.showSuccess(`Группа ${groupCode} назначена менеджеру ${this.selectedManagerForGroups.fNAME}`);
                            }
                        } else {
                            if (Array.isArray(this.groupManagerAssignments[groupCode])) {
                                const idx = this.groupManagerAssignments[groupCode].indexOf(managerId);
                                if (idx > -1) {
                                    this.groupManagerAssignments[groupCode].splice(idx, 1);
                                }
                                if (this.groupManagerAssignments[groupCode].length === 0) {
                                    delete this.groupManagerAssignments[groupCode];
                                }
                            } else if (this.groupManagerAssignments[groupCode] === managerId) {
                                delete this.groupManagerAssignments[groupCode];
                            }
                            if (showToast) {
                                this.showSuccess(`Группа ${groupCode} снята с менеджера ${this.selectedManagerForGroups.fNAME}`);
                            }
                        }
                        return true;
                    }
                    return false;
                } catch (error) {
                    console.error('Ошибка переключения группы:', error);
                    return false;
                }
            },

            async saveManagerGroups() {
                alert('Назначения сохранены автоматически');
            },

            async selectAllGroups() {
                if (!this.selectedManagerForGroups) return;
                if (!confirm(`Назначить ВСЕ группы менеджеру "${this.selectedManagerForGroups.fNAME}"?`)) return;

                let assigned = 0;
                for (const group of this.groupsWithStats) {
                    if (!group.isExcluded && !this.isGroupAssignedToSelectedManager(group.fGROUP)) {
                        const success = await this.toggleGroupForManager(group.fGROUP, true, false);
                        if (success) assigned++;
                    }
                }

                if (assigned > 0) {
                    this.showSuccess(`Назначено ${assigned} групп менеджеру ${this.selectedManagerForGroups.fNAME}`);
                }
            },

            async clearManagerGroups() {
                if (!this.selectedManagerForGroups) return;
                if (!confirm(`Снять все группы с менеджера "${this.selectedManagerForGroups.fNAME}"?`)) return;

                const managerId = this.selectedManagerForGroups.fID;
                const managerGroups = Object.entries(this.groupManagerAssignments)
                    .filter(([group, managers]) => {
                        if (Array.isArray(managers)) {
                            return managers.includes(managerId);
                        }
                        return managers === managerId;
                    })
                    .map(([group]) => group);

                let cleared = 0;
                for (const groupCode of managerGroups) {
                    const success = await this.toggleGroupForManager(groupCode, false, false);
                    if (success) cleared++;
                }

                if (cleared > 0) {
                    this.showSuccess(`Снято ${cleared} групп с менеджера ${this.selectedManagerForGroups.fNAME}`);
                }
            },

            // ===== Массовое изменение =====
            selectAllManagers() {
                this.selectedManagersForBulk = this.managers.map(m => m.fID);
            },

            selectAllGroupsForBulk() {
                this.selectedGroupsForBulk = this.groupsWithStats
                    .filter(g => !g.isExcluded)
                    .map(g => g.fGROUP);
            },

            async bulkAssignGroups() {
                if (!confirm(`ДОБАВИТЬ выбранные ${this.selectedGroupsForBulk.length} групп к ${this.selectedManagersForBulk.length} менеджерам?\n\nСуществующие назначения сохранятся.`)) {
                    return;
                }

                let updated = 0;
                for (const managerId of this.selectedManagersForBulk) {
                    for (const groupCode of this.selectedGroupsForBulk) {
                        // Проверить, не назначена ли уже эта группа этому менеджеру
                        const managers = this.groupManagerAssignments[groupCode];
                        const alreadyAssigned = managers && (
                            Array.isArray(managers) ? managers.includes(managerId) : managers === managerId
                        );
                        
                        if (!alreadyAssigned) {
                            await this.assignGroupToManager(groupCode, managerId);
                            updated++;
                        }
                    }
                }

                await this.loadGroupsWithStats();
                this.showSuccess(`Выполнено ${updated} назначений (${this.selectedManagersForBulk.length} менеджеров x ${this.selectedGroupsForBulk.length} групп)`);
            },

            async bulkReplaceGroups() {
                if (!confirm(`ЗАМЕНИТЬ все группы у ${this.selectedManagersForBulk.length} менеджеров на выбранные ${this.selectedGroupsForBulk.length} групп?\n\nСтарые назначения будут удалены!`)) {
                    return;
                }

                let cleared = 0;
                let assigned = 0;

                // Сначала очистить все группы у выбранных менеджеров
                for (const managerId of this.selectedManagersForBulk) {
                    const managerGroups = Object.entries(this.groupManagerAssignments)
                        .filter(([group, managers]) => {
                            if (Array.isArray(managers)) {
                                return managers.includes(managerId);
                            }
                            return managers === managerId;
                        })
                        .map(([group]) => group);
                    
                    for (const groupCode of managerGroups) {
                        await this.removeManagerFromGroup(groupCode, managerId);
                        cleared++;
                    }
                }

                // Затем назначить новые группы
                for (const managerId of this.selectedManagersForBulk) {
                    for (const groupCode of this.selectedGroupsForBulk) {
                        await this.assignGroupToManager(groupCode, managerId);
                        assigned++;
                    }
                }

                await this.loadGroupsWithStats();
                this.showSuccess(`Замена завершена: удалено ${cleared}, назначено ${assigned} групп`);
            },

            async bulkClearGroups() {
                if (!confirm(`ОЧИСТИТЬ все группы у ${this.selectedManagersForBulk.length} менеджеров?`)) {
                    return;
                }

                let cleared = 0;
                for (const managerId of this.selectedManagersForBulk) {
                    const managerGroups = Object.entries(this.groupManagerAssignments)
                        .filter(([group, managers]) => {
                            if (Array.isArray(managers)) {
                                return managers.includes(managerId);
                            }
                            return managers === managerId;
                        })
                        .map(([group]) => group);
                    
                    for (const groupCode of managerGroups) {
                        await this.removeManagerFromGroup(groupCode, managerId);
                        cleared++;
                    }
                }

                await this.loadGroupsWithStats();
                this.showSuccess(`Удалено ${cleared} назначений у ${this.selectedManagersForBulk.length} менеджеров`);
            },

            async assignGroupToManager(groupCode, managerId) {
                const response = await fetch('/api/settings/group-manager-assignments/set', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ groupCode, managerId })
                });

                const result = await response.json();
                if (result.success) {
                    // Обновить локальные данные
                    if (!this.groupManagerAssignments[groupCode]) {
                        this.groupManagerAssignments[groupCode] = [];
                    }
                    if (!Array.isArray(this.groupManagerAssignments[groupCode])) {
                        this.groupManagerAssignments[groupCode] = [this.groupManagerAssignments[groupCode]];
                    }
                    if (!this.groupManagerAssignments[groupCode].includes(managerId)) {
                        this.groupManagerAssignments[groupCode].push(managerId);
                    }
                }
            },

            async removeManagerFromGroup(groupCode, managerId) {
                const response = await fetch('/api/settings/group-manager-assignments/remove', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ groupCode, managerId })
                });

                const result = await response.json();
                if (result.success) {
                    // Обновить локальные данные
                    if (Array.isArray(this.groupManagerAssignments[groupCode])) {
                        const idx = this.groupManagerAssignments[groupCode].indexOf(managerId);
                        if (idx > -1) {
                            this.groupManagerAssignments[groupCode].splice(idx, 1);
                        }
                        if (this.groupManagerAssignments[groupCode].length === 0) {
                            delete this.groupManagerAssignments[groupCode];
                        }
                    } else if (this.groupManagerAssignments[groupCode] === managerId) {
                        delete this.groupManagerAssignments[groupCode];
                    }
                }
            },

            // Загрузка всех групп товаров
            async loadProductGroups() {
                try {
                    const response = await fetch('/api/settings/product-groups');
                    const result = await response.json();
                    if (result.success) {
                        this.productGroups = result.data;
                    }
                } catch (error) {
                    console.error('Ошибка загрузки групп товаров:', error);
                }
            },

            // Загрузка выбранных групп товаров
            async loadSelectedProductGroups() {
                try {
                    const response = await fetch('/api/settings/selected-product-groups');
                    const result = await response.json();
                    if (result.success) {
                        this.selectedProductGroups = result.data || [];
                    }
                } catch (error) {
                    console.error('Ошибка загрузки выбранных групп товаров:', error);
                }
            },

            // Переключить выбор группы товара
            toggleProductGroup(groupCode) {
                const index = this.selectedProductGroups.indexOf(groupCode);
                if (index > -1) {
                    this.selectedProductGroups.splice(index, 1);
                } else {
                    this.selectedProductGroups.push(groupCode);
                }
            },

            // Переключить все группы товаров
            toggleAllProductGroups(event) {
                if (event.target.checked) {
                    this.selectedProductGroups = this.productGroups.map(g => g.fGROUP);
                } else {
                    this.selectedProductGroups = [];
                }
            },

            // Выбрать все группы товаров
            selectAllProductGroups() {
                this.selectedProductGroups = this.productGroups.map(g => g.fGROUP);
            },

            // Очистить выбор групп товаров
            clearAllProductGroups() {
                this.selectedProductGroups = [];
            },

            // Получить общее количество товаров в выбранных группах
            getTotalSelectedProducts() {
                return this.productGroups
                    .filter(g => this.selectedProductGroups.includes(g.fGROUP))
                    .reduce((sum, g) => sum + g.product_count, 0);
            },

            // Сохранить выбранные группы товаров
            async saveProductGroups() {
                try {
                    const response = await fetch('/api/settings/selected-product-groups/set', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ selectedGroups: this.selectedProductGroups })
                    });

                    const result = await response.json();
                    if (result.success) {
                        this.showSuccess('Выбор групп товаров сохранен!');
                    } else {
                        alert('Ошибка при сохранении: ' + result.message);
                    }
                } catch (error) {
                    console.error('Ошибка сохранения групп товаров:', error);
                    alert('Ошибка при сохранении групп товаров');
                }
            },

            formatCurrency(num) {
                return new Intl.NumberFormat('ru-RU', {
                    style: 'currency',
                    currency: 'AMD',
                    minimumFractionDigits: 0
                }).format(num);
            },

            // ==================== ПОЛЬЗОВАТЕЛИ ====================
            async loadUsers() {
                try {
                    const response = await fetch('/api/users');
                    const result = await response.json();
                    if (result.success) {
                        this.users = result.data;
                    }
                } catch (error) {
                    console.error('Ошибка загрузки пользователей:', error);
                }
            },

            newUser() {
                this.userForm = { editing: false, username: '', display_name: '', role: 'user', areas: [], password: '' };
                new bootstrap.Modal(document.getElementById('userModal')).show();
            },

            editUser(u) {
                this.userForm = {
                    editing: true,
                    username: u.username,
                    display_name: u.display_name || '',
                    role: u.role || 'user',
                    areas: Array.isArray(u.areas) ? [...u.areas] : [],
                    password: ''
                };
                new bootstrap.Modal(document.getElementById('userModal')).show();
            },

            toggleUserArea(code) {
                const i = this.userForm.areas.indexOf(code);
                if (i === -1) {
                    this.userForm.areas.push(code);
                } else {
                    this.userForm.areas.splice(i, 1);
                }
            },

            userSelectAllAreas() {
                this.userForm.areas = this.salesAreas.map(a => a.code);
            },

            async saveUser() {
                const f = this.userForm;
                if (!f.username.trim()) { alert('Укажите логин'); return; }
                if (!f.editing && !f.password) { alert('Для нового пользователя нужен пароль'); return; }
                if (f.role !== 'admin' && f.areas.length === 0) {
                    alert('Выберите хотя бы одну территорию для пользователя');
                    return;
                }
                try {
                    const response = await fetch('/api/users', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            username: f.username.trim(),
                            display_name: f.display_name.trim(),
                            role: f.role,
                            areas: f.role === 'admin' ? [] : f.areas,
                            password: f.password || undefined
                        })
                    });
                    const result = await response.json();
                    if (result.success) {
                        bootstrap.Modal.getInstance(document.getElementById('userModal')).hide();
                        this.showSuccess('Пользователь сохранён');
                        await this.loadUsers();
                    } else {
                        alert('Ошибка: ' + (result.error || 'не удалось сохранить'));
                    }
                } catch (error) {
                    console.error('Ошибка сохранения пользователя:', error);
                    alert('Ошибка при сохранении пользователя');
                }
            },

            async deleteUser(username) {
                if (!confirm(`Удалить пользователя «${username}»?`)) return;
                try {
                    const response = await fetch('/api/users/delete', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ username })
                    });
                    const result = await response.json();
                    if (result.success) {
                        this.showSuccess('Пользователь удалён');
                        await this.loadUsers();
                    } else {
                        alert('Ошибка: ' + (result.error || 'не удалось удалить'));
                    }
                } catch (error) {
                    console.error('Ошибка удаления пользователя:', error);
                    alert('Ошибка при удалении пользователя');
                }
            }
        }
    }
