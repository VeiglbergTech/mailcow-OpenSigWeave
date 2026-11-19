(function () {
  'use strict';
  var catalog = { domains: [], signatures: [], groups: [], rules: [] };
  var editor = null;
  var signatureModal;
  var groupModal;
  var ruleModal;
  var domainModal;
  var editingGroupMembers = {};
  var editingRuleConditions = [];
  var $ = function (id) { return document.getElementById(id); };

  function node(tag, cls, value) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (value !== undefined) el.textContent = value;
    return el;
  }

  function notify(message, type) {
    var el = $('osw-alert');
    el.className = 'alert alert-' + (type || 'info');
    el.textContent = message;
    el.classList.remove('d-none');
  }

  async function api(method, path, payload) {
    var response = await fetch('/inc/ajax/opensigweave.php', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': oswCsrfToken },
      body: JSON.stringify({ method: method, path: path, payload: payload || {} })
    });
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.detail || ('Request failed: ' + response.status));
    return data;
  }

  function fillSelect(id, values, labelKey, valueKey, selected) {
    var select = $(id);
    select.replaceChildren();
    values.forEach(function (item) {
      var option = node('option', '', item[labelKey]);
      option.value = String(item[valueKey]);
      option.selected = !!selected && selected.indexOf(String(item[valueKey])) !== -1;
      select.append(option);
    });
  }

  function selectedValues(id) {
    return Array.from($(id).selectedOptions).map(function (option) { return option.value; });
  }

  function renderDomains() {
    var root = $('osw-domains');
    root.replaceChildren();
    if (!catalog.domains.length) {
      root.append(node('div', 'text-muted', 'Noch keine Domains konfiguriert.'));
      return;
    }
    catalog.domains.forEach(function (domain) {
      var row = node('div', 'osw-domain-row');
      row.append(node('strong', '', domain.domain_name));
      row.append(node('span', domain.is_active ? 'badge text-bg-success' : 'badge text-bg-secondary',
        domain.is_active ? 'Aktiv' : 'Inaktiv'));
      var edit = node('button', 'btn btn-sm btn-outline-light ms-auto', 'Bearbeiten');
      edit.type = 'button';
      edit.addEventListener('click', function () { openDomain(domain); });
      var remove = node('button', 'btn btn-sm btn-outline-danger', 'Löschen');
      remove.type = 'button';
      remove.addEventListener('click', async function () {
        if (!window.confirm('Domain ' + domain.domain_name + ' löschen? Zugeordnete Signaturen oder Gruppen müssen zuerst entfernt werden.')) return;
        try {
          await api('DELETE', '/admin/domains/' + domain.id);
          await refresh();
          notify('Domain gelöscht.', 'success');
        } catch (error) { notify(error.message, 'danger'); }
      });
      row.append(edit, remove);
      root.append(row);
    });
  }

  function openDomain(domain) {
    $('osw-domain-id').value = domain ? String(domain.id) : '';
    $('osw-domain-title').textContent = domain ? 'Domain bearbeiten' : 'Domain hinzufügen';
    $('osw-domain-name').value = domain ? domain.domain_name : '';
    $('osw-domain-active').checked = domain ? !!domain.is_active : true;
    $('osw-domain-overrides').checked = domain ? !!domain.allow_overrides : true;
    $('osw-domain-replies').checked = domain ? !!domain.inject_on_replies : false;
    $('osw-domain-trim').checked = domain ? !!domain.trim_whitespace : true;
    $('osw-domain-strip-device').checked = domain ? !!domain.strip_device_signatures : false;
    domainModal.show();
  }

  async function saveDomain() {
    var id = $('osw-domain-id').value;
    var payload = {
      domain_name: $('osw-domain-name').value.trim(),
      is_active: $('osw-domain-active').checked,
      allow_overrides: $('osw-domain-overrides').checked,
      inject_on_replies: $('osw-domain-replies').checked,
      trim_whitespace: $('osw-domain-trim').checked,
      strip_device_signatures: $('osw-domain-strip-device').checked
    };
    if (!payload.domain_name) {
      notify('Bitte einen Domainnamen eingeben.', 'warning');
      return;
    }
    var button = $('osw-save-domain');
    button.disabled = true;
    try {
      await api(id ? 'PUT' : 'POST', id ? '/admin/domains/' + id : '/admin/domains', payload);
      domainModal.hide();
      await refresh();
      notify(id ? 'Domain aktualisiert.' : 'Domain hinzugefügt.', 'success');
    } catch (error) { notify(error.message, 'danger'); }
    finally { button.disabled = false; }
  }

  function renderSignatures() {
    var root = $('osw-signatures');
    root.replaceChildren();
    $('osw-signature-count').textContent = String(catalog.signatures.length);
    if (!catalog.signatures.length) {
      var empty = node('tr');
      var cell = node('td', 'text-muted text-center py-4', 'Noch keine Signaturen angelegt.');
      cell.colSpan = 5;
      empty.append(cell);
      root.append(empty);
      return;
    }
    catalog.signatures.forEach(function (signature) {
      var row = node('tr');
      row.append(node('td', 'fw-semibold', signature.name));
      row.append(node('td', '', signature.mode + ' · Gewicht ' + signature.weight));
      row.append(node('td', 'text-muted small', signature.domains.length ? signature.domains.join(', ') : 'Global'));
      row.append(node('td', '', signature.is_active ? 'Aktiv' : 'Deaktiviert'));
      var actions = node('td', 'text-end');
      var edit = node('button', 'btn btn-sm btn-outline-light me-2', 'Bearbeiten');
      edit.type = 'button';
      edit.addEventListener('click', function () { openSignature(signature); });
      var remove = node('button', 'btn btn-sm btn-outline-danger', 'Löschen');
      remove.type = 'button';
      remove.addEventListener('click', function () { deleteSignature(signature); });
      actions.append(edit, remove);
      row.append(actions);
      root.append(row);
    });
  }

  function renderGroups() {
    var root = $('osw-groups');
    root.replaceChildren();
    if (!catalog.groups.length) {
      root.append(node('div', 'text-muted', 'Noch keine Gruppen angelegt.'));
      return;
    }
    catalog.groups.forEach(function (group) {
      var row = node('div', 'osw-domain-row');
      var info = node('div');
      info.append(node('strong', '', group.name));
      info.append(node('div', 'small text-muted', group.members.length + ' Signaturen · ' +
        (group.domains.length ? group.domains.join(', ') : 'alle Domains')));
      var edit = node('button', 'btn btn-sm btn-outline-light me-2', 'Bearbeiten');
      edit.type = 'button';
      edit.addEventListener('click', function () { openGroup(group); });
      var remove = node('button', 'btn btn-sm btn-outline-danger', 'Löschen');
      remove.type = 'button';
      remove.addEventListener('click', async function () {
        if (!window.confirm('Gruppe "' + group.name + '" löschen?')) return;
        try { await api('DELETE', '/admin/groups/' + group.id); await refresh(); notify('Gruppe gelöscht.', 'success'); }
        catch (error) { notify(error.message, 'danger'); }
      });
      row.append(info, edit, remove);
      root.append(row);
    });
  }

  function renderRules() {
    var root = $('osw-rules');
    root.replaceChildren();
    if (!catalog.rules.length) {
      root.append(node('div', 'text-muted', 'Noch keine Regeln angelegt.'));
      return;
    }
    catalog.rules.forEach(function (rule) {
      var row = node('div', 'osw-domain-row');
      var info = node('div');
      info.append(node('strong', '', rule.name));
      info.append(node('div', 'small text-muted', 'Priorität ' + rule.priority + ' · ' +
        rule.action_mode + ' · ' + rule.conditions.length + ' Bedingungen'));
      var edit = node('button', 'btn btn-sm btn-outline-light me-2', 'Bearbeiten');
      edit.type = 'button';
      edit.addEventListener('click', function () { openRule(rule); });
      var remove = node('button', 'btn btn-sm btn-outline-danger', 'Löschen');
      remove.type = 'button';
      remove.addEventListener('click', async function () {
        if (!window.confirm('Regel "' + rule.name + '" löschen?')) return;
        try { await api('DELETE', '/admin/rules/' + rule.id); await refresh(); notify('Regel gelöscht.', 'success'); }
        catch (error) { notify(error.message, 'danger'); }
      });
      row.append(info, edit, remove);
      root.append(row);
    });
  }

  async function refresh() {
    try {
      var results = await Promise.all([
        api('GET', '/admin/catalog'),
        api('GET', '/admin/groups'),
        api('GET', '/admin/rules')
      ]);
      catalog = { domains: results[0].domains, signatures: results[0].signatures,
        groups: results[1], rules: results[2] };
      renderDomains();
      renderSignatures();
      renderGroups();
      renderRules();
    } catch (error) {
      notify(error.message, 'danger');
    }
  }

  function openSignature(signature) {
    $('osw-signature-id').value = signature ? signature.id : '';
    $('osw-preview-panel').classList.add('d-none');
    $('osw-version-panel').classList.add('d-none');
    $('osw-preview-frame').removeAttribute('srcdoc');
    $('osw-version-list').replaceChildren();
    $('osw-show-versions').disabled = !signature;
    $('osw-modal-title').textContent = signature ? 'Signatur bearbeiten' : 'Neue Signatur';
    $('osw-signature-name').value = signature ? signature.name : '';
    $('osw-signature-mode').value = signature ? signature.mode : 'AUTO';
    $('osw-signature-active').checked = signature ? !!signature.is_active : true;
    $('osw-signature-variant-enabled').checked = signature ? !!signature.variant_enabled : true;
    $('osw-signature-weight').value = signature ? signature.weight : 1;
    var domainSelect = $('osw-signature-domains');
    domainSelect.replaceChildren();
    catalog.domains.forEach(function (domain) {
      var option = node('option', '', domain.domain_name);
      option.value = String(domain.id);
      option.selected = !!signature && signature.domains.indexOf(domain.domain_name) !== -1;
      domainSelect.append(option);
    });
    if (editor) {
      editor.setData(signature ? signature.html_content :
        '<table><tbody><tr><td>{{ first_name }} {{ last_name }}</td></tr><tr><td>{{ title }}</td></tr><tr><td>{{ phone }}</td></tr><tr><td>{{ email }}</td></tr></tbody></table>');
    }
    signatureModal.show();
  }

  async function saveSignature() {
    var id = $('osw-signature-id').value;
    var payload = {
      name: $('osw-signature-name').value.trim(),
      mode: $('osw-signature-mode').value,
      is_active: $('osw-signature-active').checked,
      variant_enabled: $('osw-signature-variant-enabled').checked,
      weight: Math.max(1, Number($('osw-signature-weight').value) || 1),
      domain_ids: Array.from($('osw-signature-domains').selectedOptions).map(function (o) { return Number(o.value); }),
      html_content: editor ? editor.getData() : ''
    };
    if (!payload.name) { notify('Bitte einen Signaturnamen eingeben.', 'warning'); return; }
    var button = $('osw-save-signature');
    button.disabled = true;
    try {
      await api(id ? 'PUT' : 'POST', id ? '/admin/signatures/' + id : '/admin/signatures', payload);
      signatureModal.hide();
      await refresh();
      notify('Signatur gespeichert.', 'success');
    } catch (error) { notify(error.message, 'danger'); }
    finally { button.disabled = false; }
  }

  async function previewSignature() {
    var html = editor ? editor.getData() : '';
    if (!html.trim()) {
      notify('Bitte zuerst Signaturinhalt eingeben.', 'warning');
      return;
    }
    var selected = Array.from($('osw-signature-domains').selectedOptions);
    var domainId = selected.length ? Number(selected[0].value) : null;
    try {
      var result = await api('POST', '/admin/preview', { html_content: html, domain_id: domainId });
      $('osw-preview-frame').srcdoc = result.html || '';
      $('osw-preview-panel').classList.remove('d-none');
    } catch (error) {
      notify(error.message, 'danger');
    }
  }

  async function loadVersions() {
    var signatureId = $('osw-signature-id').value;
    if (!signatureId) {
      notify('Versionsverlauf ist erst nach dem Speichern verfügbar.', 'info');
      return;
    }
    try {
      var result = await api('GET', '/admin/signatures/' + signatureId + '/versions');
      var root = $('osw-version-list');
      root.replaceChildren();
      if (!result.versions.length) {
        var emptyRow = node('tr');
        var emptyCell = node('td', 'text-muted', 'Keine Versionen vorhanden.');
        emptyCell.colSpan = 4;
        emptyRow.append(emptyCell);
        root.append(emptyRow);
      }
      result.versions.forEach(function (version, index) {
        var row = node('tr');
        row.append(node('td', 'fw-semibold', 'v' + version.version_number));
        var created = version.created_at ? new Date(version.created_at).toLocaleString() : '—';
        row.append(node('td', 'small', created));
        row.append(node('td', 'small text-muted', version.created_by || '—'));
        var action = node('td', 'text-end');
        if (index === 0) {
          action.append(node('span', 'badge text-bg-secondary', 'Aktuell'));
        } else {
          var restore = node('button', 'btn btn-sm btn-outline-warning', 'Wiederherstellen');
          restore.type = 'button';
          restore.addEventListener('click', function () { restoreVersion(version); });
          action.append(restore);
        }
        row.append(action);
        root.append(row);
      });
      $('osw-version-panel').classList.remove('d-none');
    } catch (error) {
      notify(error.message, 'danger');
    }
  }

  async function restoreVersion(version) {
    var signatureId = $('osw-signature-id').value;
    if (!signatureId || !window.confirm('Version ' + version.version_number + ' als neue aktuelle Version wiederherstellen?')) return;
    try {
      await api('POST', '/admin/signatures/' + signatureId + '/versions/' + version.id + '/restore', {});
      await refresh();
      var current = catalog.signatures.find(function (item) { return item.id === signatureId; });
      if (current && editor) editor.setData(current.html_content || '');
      await loadVersions();
      notify('Version wiederhergestellt. Der bisherige Verlauf bleibt erhalten.', 'success');
    } catch (error) {
      notify(error.message, 'danger');
    }
  }

  async function deleteSignature(signature) {
    if (!window.confirm('Signatur "' + signature.name + '" wirklich löschen?')) return;
    try {
      await api('DELETE', '/admin/signatures/' + signature.id);
      await refresh();
      notify('Signatur gelöscht.', 'success');
    } catch (error) { notify(error.message, 'danger'); }
  }

  async function saveGroup() {
    var groupId = $('osw-group-id').value;
    var payload = {
      name: $('osw-group-name').value.trim(),
      is_active: $('osw-group-active').checked,
      domain_ids: selectedValues('osw-group-domains').map(Number),
      members: Array.from($('osw-group-member-list').querySelectorAll('[data-signature-id]'))
        .filter(function (entry) { return entry.querySelector('[data-member-selected]').checked; })
        .map(function (entry) {
          var id = entry.getAttribute('data-signature-id');
          var current = editingGroupMembers[id];
          return {
            signature_id: id,
            weight: Math.max(1, Number(entry.querySelector('[data-member-weight]').value) || 1),
            is_enabled: current ? current.is_enabled : true
          };
        })
    };
    if (!payload.name || !payload.members.length) {
      notify('Bitte einen Gruppennamen und mindestens eine Signatur auswählen.', 'warning');
      return;
    }
    try {
      await api(groupId ? 'PUT' : 'POST', groupId ? '/admin/groups/' + groupId : '/admin/groups', payload);
      groupModal.hide();
      await refresh();
      notify('Gruppe gespeichert.', 'success');
    } catch (error) { notify(error.message, 'danger'); }
  }

  async function saveRule() {
    var ruleId = $('osw-rule-id').value;
    var action = $('osw-rule-action').value;
    var payload = {
      name: $('osw-rule-name').value.trim(),
      priority: Number($('osw-rule-priority').value) || 0,
      action_mode: action,
      signature_id: $('osw-rule-signature').value || null,
      group_id: action === 'GROUP' ? ($('osw-rule-group').value || null) : null,
      is_enabled: $('osw-rule-enabled').checked,
      conditions: editingRuleConditions.map(function (condition) {
        return { kind: condition.kind, key: condition.key.trim(), value: condition.value.trim() };
      })
    };
    var invalidCondition = payload.conditions.some(function (condition) {
      return !condition.value || (['claim_equals', 'attribute_equals'].indexOf(condition.kind) !== -1 && !condition.key);
    });
    if (!payload.name || (action === 'GROUP' && !payload.group_id) || invalidCondition) {
      notify('Bitte Regelname, Gruppenaktion und alle Bedingungsfelder prüfen.', 'warning');
      return;
    }
    try {
      await api(ruleId ? 'PUT' : 'POST', ruleId ? '/admin/rules/' + ruleId : '/admin/rules', payload);
      ruleModal.hide();
      await refresh();
      notify('Regel gespeichert.', 'success');
    } catch (error) { notify(error.message, 'danger'); }
  }

  function openGroup(group) {
    $('osw-group-id').value = group ? group.id : '';
    $('osw-group-title').textContent = group ? 'Signaturgruppe bearbeiten' : 'Signaturgruppe anlegen';
    $('osw-group-name').value = group ? group.name : '';
    $('osw-group-active').checked = group ? !!group.is_active : true;
    editingGroupMembers = {};
    if (group) group.members.forEach(function (member) { editingGroupMembers[member.signature_id] = member; });
    var memberList = $('osw-group-member-list');
    memberList.replaceChildren();
    catalog.signatures.forEach(function (signature) {
      var current = editingGroupMembers[signature.id];
      var row = node('div', 'd-flex align-items-center justify-content-between gap-3 border rounded p-2 mb-2');
      row.setAttribute('data-signature-id', String(signature.id));
      var check = node('input', 'form-check-input me-2');
      check.type = 'checkbox';
      check.checked = !!current;
      check.setAttribute('data-member-selected', 'true');
      var label = node('label', 'form-check-label', signature.name);
      label.htmlFor = 'osw-group-member-' + signature.id;
      check.id = label.htmlFor;
      var left = node('div', 'form-check mb-0');
      left.append(check, label);
      var weight = node('input', 'form-control form-control-sm');
      weight.type = 'number';
      weight.min = '1';
      weight.max = '100000';
      weight.value = String(current ? current.weight : 1);
      weight.style.width = '110px';
      weight.setAttribute('aria-label', 'Gewichtung für ' + signature.name);
      weight.setAttribute('data-member-weight', 'true');
      row.append(left, weight);
      memberList.append(row);
    });
    fillSelect('osw-group-domains', catalog.domains, 'domain_name', 'id',
      group ? catalog.domains.filter(function (d) { return group.domains.indexOf(d.domain_name) !== -1; }).map(function (d) { return d.id; }) : []);
    groupModal.show();
  }

  var ruleConditionTypes = [
    { value: 'sender_equals', label: 'Absender exakt' },
    { value: 'domain_equals', label: 'Domain exakt' },
    { value: 'group_contains', label: 'Gruppe enthält' },
    { value: 'claim_equals', label: 'Claim exakt' },
    { value: 'attribute_equals', label: 'Attribut exakt' }
  ];

  function renderRuleConditions() {
    var root = $('osw-rule-conditions');
    root.replaceChildren();
    if (!editingRuleConditions.length) {
      root.append(node('div', 'small text-muted', 'Keine Bedingungen: Die Regel trifft unabhängig vom Absenderkontext zu.'));
      return;
    }
    editingRuleConditions.forEach(function (condition, index) {
      var row = node('div', 'row g-2 align-items-center mb-2');
      var typeColumn = node('div', 'col-md-4');
      var type = node('select', 'form-select form-select-sm');
      type.setAttribute('aria-label', 'Bedingungstyp ' + (index + 1));
      ruleConditionTypes.forEach(function (item) {
        var option = node('option', '', item.label);
        option.value = item.value;
        option.selected = condition.kind === item.value;
        type.append(option);
      });
      type.addEventListener('change', function () {
        editingRuleConditions[index].kind = type.value;
        if (['claim_equals', 'attribute_equals'].indexOf(type.value) === -1) editingRuleConditions[index].key = '';
        renderRuleConditions();
      });
      typeColumn.append(type);

      var keyColumn = node('div', 'col-md-3');
      var key = node('input', 'form-control form-control-sm');
      key.type = 'text';
      key.value = condition.key || '';
      key.placeholder = 'Schlüssel (Claim/Attribut)';
      key.setAttribute('aria-label', 'Schlüssel für Bedingung ' + (index + 1));
      key.disabled = ['claim_equals', 'attribute_equals'].indexOf(condition.kind) === -1;
      key.addEventListener('input', function () { editingRuleConditions[index].key = key.value; });
      keyColumn.append(key);

      var valueColumn = node('div', 'col');
      var value = node('input', 'form-control form-control-sm');
      value.type = 'text';
      value.value = condition.value || '';
      value.placeholder = 'Vergleichswert';
      value.setAttribute('aria-label', 'Wert für Bedingung ' + (index + 1));
      value.addEventListener('input', function () { editingRuleConditions[index].value = value.value; });
      valueColumn.append(value);

      var removeColumn = node('div', 'col-auto');
      var remove = node('button', 'btn btn-sm btn-outline-danger', 'Entfernen');
      remove.type = 'button';
      remove.setAttribute('aria-label', 'Bedingung ' + (index + 1) + ' entfernen');
      remove.addEventListener('click', function () {
        editingRuleConditions.splice(index, 1);
        renderRuleConditions();
      });
      removeColumn.append(remove);
      row.append(typeColumn, keyColumn, valueColumn, removeColumn);
      root.append(row);
    });
  }

  function openRule(rule) {
    $('osw-rule-id').value = rule ? rule.id : '';
    editingRuleConditions = rule ? rule.conditions.map(function (condition) { return { kind: condition.kind, key: condition.key, value: condition.value }; }) : [];
    $('osw-rule-title').textContent = rule ? 'Regel bearbeiten' : 'Regel anlegen';
    $('osw-rule-name').value = rule ? rule.name : '';
    $('osw-rule-priority').value = rule ? String(rule.priority) : '0';
    $('osw-rule-action').value = rule ? rule.action_mode : 'AUTO';
    $('osw-rule-enabled').checked = rule ? !!rule.is_enabled : true;
    renderRuleConditions();
    fillSelect('osw-rule-signature', [{ id: '', name: 'Keine' }].concat(catalog.signatures), 'name', 'id',
      rule ? [rule.signature_id || ''] : ['']);
    fillSelect('osw-rule-group', [{ id: '', name: 'Bitte auswählen' }].concat(catalog.groups), 'name', 'id',
      rule ? [rule.group_id || ''] : ['']);
    ruleModal.show();
  }

  document.addEventListener('DOMContentLoaded', async function () {
    signatureModal = new bootstrap.Modal($('osw-signature-modal'));
    domainModal = new bootstrap.Modal($('osw-domain-modal'));
    groupModal = new bootstrap.Modal($('osw-group-modal'));
    ruleModal = new bootstrap.Modal($('osw-rule-modal'));
    try {
      editor = await window.OpenSigWeaveEditor.create($('osw-signature-html'), {
        placeholder: 'E-Mail-Signatur erstellen …'
      });
    } catch (error) {
      notify('CKEditor 5 konnte nicht geladen werden: ' + error.message, 'danger');
    }
    $('osw-refresh').addEventListener('click', refresh);
    $('osw-new-signature').addEventListener('click', function () { openSignature(null); });
    $('osw-new-domain').addEventListener('click', function () { openDomain(null); });
    $('osw-save-domain').addEventListener('click', saveDomain);
    $('osw-save-signature').addEventListener('click', saveSignature);
    $('osw-preview-signature').addEventListener('click', previewSignature);
    $('osw-show-versions').addEventListener('click', loadVersions);
    $('osw-new-group').addEventListener('click', function () { openGroup(null); });
    $('osw-save-group').addEventListener('click', saveGroup);
    $('osw-new-rule').addEventListener('click', function () { openRule(null); });
    $('osw-add-rule-condition').addEventListener('click', function () {
      editingRuleConditions.push({ kind: 'domain_equals', key: '', value: '' });
      renderRuleConditions();
    });
    $('osw-save-rule').addEventListener('click', saveRule);
    await refresh();
  });
})();
