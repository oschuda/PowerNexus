// ========================================
// UPS Groups Management (Phase 5.6)
// ========================================

/**
 * Load and display all UPS groups
 */
async function loadGroups() {
    try {
        const response = await fetch(API_GROUPS);
        const data = await response.json();
        
        if (!response.ok) {
            console.error('Error loading groups:', data);
            showAlert(T('group.load_failed', { error: data.error }), 'error');
            return;
        }
        
        renderGroupsGrid(data.groups || []);
    } catch (error) {
        console.error('Error loading groups:', error);
        showAlert(T('group.load_failed', { error: error.message }), 'error');
    }
}

/**
 * Render groups as cards in the grid
 */
function renderGroupsGrid(groups) {
    const grid = document.getElementById('groupsGrid');
    if (!grid) return;
    
    if (groups.length === 0) {
        grid.innerHTML = `
            <div class="card" style="grid-column: 1 / -1; text-align: center; padding: 40px;">
                <p style="color: #94a3b8; font-size: 1rem;">${T('group.empty')}</p>
                <button class="chart-btn" onclick="openGroupModal(null)" style="background: #a855f7; color: white; border: none; cursor: pointer; margin-top: 12px;">
                    ${T('group.add_first')}
                </button>
            </div>
        `;
        return;
    }
    
    grid.innerHTML = groups.map(group => `
        <div class="card">
            <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 12px;">
                <div>
                    <h3>${escapeHtml(group.name)}</h3>
                    <p style="color: #94a3b8; font-size: 0.85rem; margin-top: 4px;">
                        ${group.member_count === 1 ? T('group.member_one') : T('group.member_many', { count: group.member_count })}
                    </p>
                </div>
                <div style="display: flex; gap: 6px;">
                    <button class="chart-btn" onclick="openGroupModal(${group.id})" style="background: #3b82f6; color: white;">✏️</button>
                    <button class="chart-btn" onclick="deleteGroupConfirm(${group.id}, '${escapeHtml(group.name)}')" style="background: #ef4444; color: white;">🗑️</button>
                </div>
            </div>
            <p style="color: #cbd5e1; font-size: 0.85rem; margin-bottom: 12px;">
                ${group.description ? escapeHtml(group.description) : '<em style="color: #64748b;">' + T('group.no_description') + '</em>'}
            </p>
            <div style="background: rgba(255,255,255,0.05); padding: 8px 12px; border-radius: 6px; font-size: 0.8rem; color: #94a3b8;">
                <strong>${T('group.members_label')}</strong> ${group.members && group.members.length > 0 ? group.members.join(', ') : T('common.none')}
            </div>
        </div>
    `).join('');
}

/**
 * Open group modal for create or edit
 */
async function openGroupModal(groupId) {
    const modal = document.getElementById('groupModal');
    if (!modal) return;
    
    const titleEl = document.getElementById('groupModalTitle');
    const nameEl = document.getElementById('groupName');
    const descriptionEl = document.getElementById('groupDescription');
    const checkboxContainer = document.getElementById('groupMembersCheckbox');
    const deleteBtn = document.getElementById('groupDeleteBtn');
    
    // Clear form
    nameEl.value = '';
    descriptionEl.value = '';
    nameEl.disabled = false;
    
    // Load available hosts for checkboxes
    try {
        const hostsResponse = await fetch(API_HOSTS);
        const hostsData = await hostsResponse.json();
        const hostsList = hostsData.hosts || [];
        
        if (groupId === null) {
            // Create mode
            titleEl.textContent = '(neu)';
            deleteBtn.style.display = 'none';
            checkboxContainer.innerHTML = hostsList.length > 0 
                ? hostsList.map(host => `
                    <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                        <input type="checkbox" value="${host.name}" class="group-member-checkbox">
                        ${escapeHtml(host.name)}
                    </label>
                `).join('')
                : '<p style="color: #94a3b8;">' + T('group.no_hosts') + '</p>';
        } else {
            // Edit mode
            const groupResponse = await fetch(`${API_GROUPS}/${groupId}`);
            const groupData = await groupResponse.json();
            const group = groupData || {};
            
            titleEl.textContent = T('group.edit_suffix', { name: group.name });
            nameEl.value = group.name || '';
            nameEl.disabled = true; // Prevent renaming for now
            descriptionEl.value = group.description || '';
            deleteBtn.style.display = 'inline-block';
            deleteBtn.onclick = () => deleteGroupConfirm(groupId, group.name);
            
            // Render checkboxes with group membership
            const groupMembers = group.members || [];
            checkboxContainer.innerHTML = hostsList.length > 0
                ? hostsList.map(host => `
                    <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                        <input type="checkbox" value="${host.name}" class="group-member-checkbox" 
                            ${groupMembers.includes(host.name) ? 'checked' : ''}>
                        ${escapeHtml(host.name)}
                    </label>
                `).join('')
                : '<p style="color: #94a3b8;">' + T('group.no_hosts') + '</p>';
        }
        
    } catch (error) {
        console.error('Error loading hosts:', error);
        checkboxContainer.innerHTML = `<p style="color: #ef4444;">${T('group.hosts_failed', { error: escapeHtml(error.message) })}</p>`;
    }
    
    modal.classList.add('show');
}

/**
 * Close group modal
 */
function closeGroupModal() {
    const modal = document.getElementById('groupModal');
    if (modal) modal.classList.remove('show');
}

/**
 * Save group (create or update)
 */
async function saveGroupModal() {
    const nameEl = document.getElementById('groupName');
    const descriptionEl = document.getElementById('groupDescription');
    const name = nameEl.value.trim();
    const description = descriptionEl.value.trim();
    
    if (!name) {
        showAlert(T('group.name_required'), 'error');
        return;
    }
    
    // Get selected members
    const selectedMembers = Array.from(document.querySelectorAll('.group-member-checkbox:checked'))
        .map(cb => cb.value);
    
    // Determine if create or edit mode
    const isEditMode = nameEl.disabled;
    const groupId = isEditMode ? parseInt(document.getElementById('groupModalTitle').textContent.match(/\d+/) || null) : null;
    
    // Build request payload
    const payload = {
        name: name,
        description: description
    };
    
    try {
        if (isEditMode) {
            // Update group
            const response = await fetch(`${API_GROUPS}/${groupId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            
            if (!response.ok) {
                const data = await response.json();
                showAlert(T('group.update_failed', { error: data.error }), 'error');
                return;
            }
            
            // Update members
            // First, get current members to remove old ones
            const groupResponse = await fetch(`${API_GROUPS}/${groupId}`);
            const groupData = await groupResponse.json();
            const currentMembers = groupData.members || [];
            
            // Remove members not in selection
            for (const member of currentMembers) {
                if (!selectedMembers.includes(member)) {
                    await fetch(`${API_GROUPS}/${groupId}/members/${member}`, { method: 'DELETE' });
                }
            }
            
            // Add new members
            for (const member of selectedMembers) {
                if (!currentMembers.includes(member)) {
                    await fetch(`${API_GROUPS}/${groupId}/members`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ ups_id: member })
                    });
                }
            }
            
            showAlert('✓ ' + T('group.updated', { name: name }), 'success');
        } else {
            // Create new group
            const response = await fetch(API_GROUPS, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            
            if (!response.ok) {
                const data = await response.json();
                showAlert(T('group.create_failed', { error: data.error }), 'error');
                return;
            }
            
            const newGroup = await response.json();
            
            // Add members to new group
            for (const member of selectedMembers) {
                await fetch(`${API_GROUPS}/${newGroup.id}/members`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ups_id: member })
                });
            }
            
            showAlert('✓ ' + T('group.created', { name: name, count: selectedMembers.length }), 'success');
        }
        
        closeGroupModal();
        setTimeout(loadGroups, 500);
    } catch (error) {
        console.error('Error saving group:', error);
        showAlert(T('group.save_failed', { error: error.message }), 'error');
    }
}

/**
 * Confirm and delete group
 */
function deleteGroupConfirm(groupId, groupName) {
    if (!confirm(T('group.confirm_delete', { name: groupName }))) {
        return;
    }
    
    deleteGroup(groupId);
}

/**
 * Delete group
 */
async function deleteGroup(groupId) {
    try {
        const response = await fetch(`${API_GROUPS}/${groupId}`, {
            method: 'DELETE'
        });
        
        if (!response.ok) {
            const data = await response.json();
            showAlert(T('group.delete_failed', { error: data.error }), 'error');
            return;
        }
        
        showAlert('✓ ' + T('group.deleted'), 'success');
        closeGroupModal();
        setTimeout(loadGroups, 500);
    } catch (error) {
        console.error('Error deleting group:', error);
        showAlert(T('group.delete_failed', { error: error.message }), 'error');
    }
}

// Auto-refresh groups every 60 seconds if tab is visible
setInterval(() => {
    const groupsTab = document.getElementById('tab-groups');
    if (groupsTab && groupsTab.style.display !== 'none') {
        loadGroups().catch(e => console.debug('Auto-refresh groups failed:', e));
    }
}, 60000);

// Close modal on background click
document.addEventListener('click', (e) => {
    const groupModal = document.getElementById('groupModal');
    if (groupModal && e.target === groupModal) {
        closeGroupModal();
    }
});

// ESC to close groups modal
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeGroupModal();
    }
});
