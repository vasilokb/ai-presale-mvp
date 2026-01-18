const API_BASE = "http://localhost:8080/api/v1";
const app = document.getElementById("app");

const rolesList = ["SA/BA", "Backend", "Frontend", "Data-engineer", "DevOps"];

function route() {
  const path = window.location.pathname;
  if (path.startsWith("/presales/") && path.includes("/result")) {
    const parts = path.split("/");
    const presaleId = parts[2];
    const documentId = new URLSearchParams(window.location.search).get("document_id");
    renderResultScreen(presaleId, documentId);
    return;
  }
  if (path.startsWith("/presales/")) {
    const presaleId = path.split("/")[2];
    renderPresaleDetail(presaleId);
    return;
  }
  renderPresalesList();
}

window.addEventListener("popstate", route);

function navigate(path) {
  window.history.pushState({}, "", path);
  route();
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || response.statusText);
  }
  return response.json();
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function renderPresalesList() {
  const presales = await fetchJson(`${API_BASE}/presales`);
  app.innerHTML = `
    <div class="card stack">
      <div style="display:flex; justify-content:center;">
        <button class="btn" id="createPresale">+ Создать пресейл</button>
      </div>
      <div class="presale-list" id="presaleList"></div>
    </div>
  `;
  const listEl = document.getElementById("presaleList");
  presales.forEach((presale) => {
    const card = document.createElement("div");
    card.className = "presale-card";
    card.innerHTML = `
      <div>Пресейл "${presale.name}"</div>
      <div class="menu">
        <button aria-label="menu">⋮</button>
        <ul>
          <li data-action="open">Открыть</li>
          <li data-action="rename">Переименовать</li>
          <li data-action="delete">Удалить</li>
        </ul>
      </div>
    `;
    const menuBtn = card.querySelector("button");
    const menu = card.querySelector("ul");
    menuBtn.addEventListener("click", () => {
      menu.classList.toggle("open");
    });
    menu.addEventListener("mouseleave", () => menu.classList.remove("open"));
    menu.addEventListener("click", async (event) => {
      const action = event.target.dataset.action;
      if (!action) return;
      menu.classList.remove("open");
      if (action === "open") {
        navigate(`/presales/${presale.id}`);
      }
      if (action === "rename") {
        const name = window.prompt("Новое имя пресейла:", presale.name);
        if (!name) return;
        await fetchJson(`${API_BASE}/presales/${presale.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
        });
        renderPresalesList();
      }
      if (action === "delete") {
        const confirmed = window.confirm("Удалить пресейл?");
        if (!confirmed) return;
        await fetchJson(`${API_BASE}/presales/${presale.id}`, { method: "DELETE" });
        renderPresalesList();
      }
    });
    listEl.appendChild(card);
  });

  document.getElementById("createPresale").addEventListener("click", async () => {
    const name = window.prompt("Название пресейла:");
    if (!name) return;
    await fetchJson(`${API_BASE}/presales`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    renderPresalesList();
  });
}

async function renderPresaleDetail(presaleId) {
  const [presale, files] = await Promise.all([
    fetchJson(`${API_BASE}/presales/${presaleId}`),
    fetchJson(`${API_BASE}/presales/${presaleId}/files`),
  ]);
  app.innerHTML = `
    <div class="stack">
      <div class="card stack">
        <h2>Пресейл "${presale.name}"</h2>
        <label>Ввести текст задачи:</label>
        <textarea id="promptInput" placeholder="Введите текст..."></textarea>
        <label>Специалисты</label>
        <div class="roles-box" id="rolesBox"></div>
        <div class="grid-2">
          <div>
            <div class="upload-box" id="dropZone">
              <p>Выберите файл или перетащите его сюда</p>
              <p class="status">Доступные форматы: Docx, TXT, PDF</p>
              <button class="btn secondary" id="filePickerBtn">Выбрать файлы</button>
              <input type="file" id="fileInput" multiple style="display:none" />
            </div>
            <div class="file-list" id="fileList"></div>
          </div>
          <div class="card" style="align-self:start;">
            <button class="btn" id="startAnalysis">Начать AI - анализ</button>
            <div class="spacer"></div>
            <div class="status" id="startStatus"></div>
          </div>
        </div>
      </div>
    </div>
  `;

  const rolesBox = document.getElementById("rolesBox");
  const selectedRoles = new Set();
  rolesList.forEach((role) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = role;
    chip.addEventListener("click", () => {
      if (selectedRoles.has(role)) {
        selectedRoles.delete(role);
        chip.classList.remove("selected");
      } else {
        selectedRoles.add(role);
        chip.classList.add("selected");
      }
    });
    rolesBox.appendChild(chip);
  });

  const fileList = document.getElementById("fileList");
  function renderFiles(list) {
    fileList.innerHTML = "";
    list.forEach((file) => {
      const item = document.createElement("div");
      item.className = "file-item";
      item.innerHTML = `
        <div>${file.filename} (${formatSize(file.size_bytes)})</div>
        <button data-id="${file.file_id}">✕</button>
      `;
      item.querySelector("button").addEventListener("click", async () => {
        await fetchJson(`${API_BASE}/files/${file.file_id}`, { method: "DELETE" });
        const updated = await fetchJson(`${API_BASE}/presales/${presaleId}/files`);
        renderFiles(updated);
      });
      fileList.appendChild(item);
    });
  }
  renderFiles(files);

  const fileInput = document.getElementById("fileInput");
  document.getElementById("filePickerBtn").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    await uploadFiles(fileInput.files);
    fileInput.value = "";
  });

  const dropZone = document.getElementById("dropZone");
  dropZone.addEventListener("dragover", (event) => {
    event.preventDefault();
  });
  dropZone.addEventListener("drop", async (event) => {
    event.preventDefault();
    await uploadFiles(event.dataTransfer.files);
  });

  async function uploadFiles(fileList) {
    for (const file of fileList) {
      const formData = new FormData();
      formData.append("file", file);
      await fetchJson(`${API_BASE}/files/upload?presale_id=${presaleId}`, {
        method: "POST",
        body: formData,
      });
    }
    const updated = await fetchJson(`${API_BASE}/presales/${presaleId}/files`);
    renderFiles(updated);
  }

  document.getElementById("startAnalysis").addEventListener("click", async () => {
    const prompt = document.getElementById("promptInput").value.trim();
    const statusEl = document.getElementById("startStatus");
    if (!prompt) {
      statusEl.textContent = "Введите текст задачи.";
      return;
    }
    if (selectedRoles.size === 0) {
      statusEl.textContent = "Выберите хотя бы одну роль.";
      return;
    }
    if (fileList.children.length === 0) {
      statusEl.textContent = "Загрузите хотя бы один файл.";
      return;
    }
    const payload = {
      presale_id: presaleId,
      prompt,
      params: { roles: Array.from(selectedRoles), round_to_hours: 0.5 },
    };
    const response = await fetchJson(`${API_BASE}/documents/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    navigate(`/presales/${presaleId}/result?document_id=${response.document_id}`);
  });
}

async function renderResultScreen(presaleId, documentId) {
  app.innerHTML = `
    <div class="card stack">
      <div class="status" id="resultStatus">Загрузка результата...</div>
    </div>
  `;
  if (!documentId) {
    document.getElementById("resultStatus").textContent = "Не найден документ.";
    return;
  }
  let currentResult = null;
  let editMode = false;
  let displayMode = "summary";
  let currentRole = "All";
  let documentMeta = null;

  const loadResult = async () => {
    try {
      const result = await fetchJson(`${API_BASE}/documents/${documentId}/result`);
      currentResult = result;
      renderResult();
    } catch (error) {
      if (String(error.message).includes("result_not_ready")) {
        await pollStatus();
      } else {
        document.getElementById("resultStatus").textContent = `Ошибка: ${error.message}`;
      }
    }
  };

  const pollStatus = async () => {
    const statusEl = document.getElementById("resultStatus");
    statusEl.textContent = "Ожидание результата...";
    const interval = setInterval(async () => {
      const status = await fetchJson(`${API_BASE}/documents/${documentId}/status`);
      statusEl.textContent = `Статус: ${status.status} (${status.progress}%) ${status.message}`;
      if (status.status === "done") {
        clearInterval(interval);
        loadResult();
      }
      if (status.status === "error") {
        clearInterval(interval);
        statusEl.textContent = `Ошибка: ${status.message}`;
      }
    }, 2000);
  };

  const computeCounts = (result) => {
    const epics = result.epics || [];
    let taskCount = 0;
    epics.forEach((epic) => {
      taskCount += (epic.tasks || []).length;
    });
    return { epicCount: epics.length, taskCount };
  };

  const applyRoleFilter = (tasks) => {
    if (currentRole === "All") return tasks;
    return tasks.filter((task) => task.role === currentRole);
  };

  const recalcExpected = (task) => {
    const pert = task.pert_hours || {};
    const optimistic = Number(pert.optimistic || 0);
    const mostLikely = Number(pert.most_likely || 0);
    const pessimistic = Number(pert.pessimistic || 0);
    const expected = (optimistic + 4 * mostLikely + pessimistic) / 6;
    const rounded = Math.round(expected / 0.5) * 0.5;
    pert.expected = Number(rounded.toFixed(2));
  };

  const buildSummaryRows = (result) => {
    const totals = {};
    (result.epics || []).forEach((epic) => {
      (epic.tasks || []).forEach((task) => {
        const role = task.role || "Unknown";
        if (!totals[role]) totals[role] = { optimistic: 0, most_likely: 0, pessimistic: 0, expected: 0 };
        const pert = task.pert_hours || {};
        totals[role].optimistic += Number(pert.optimistic || 0);
        totals[role].most_likely += Number(pert.most_likely || 0);
        totals[role].pessimistic += Number(pert.pessimistic || 0);
        totals[role].expected += Number(pert.expected || 0);
      });
    });
    return Object.entries(totals).map(([role, values]) => ({ role, ...values }));
  };

  const renderResult = async () => {
    if (!currentResult) return;
    if (!documentMeta) {
      documentMeta = await fetchJson(`${API_BASE}/documents/${documentId}`);
    }
    const counts = computeCounts(currentResult);
    const versionsData = await fetchJson(`${API_BASE}/documents/${documentId}/versions`);
    const versionOptions = versionsData.versions
      .map((v) => `<option value="${v}" ${v === currentResult.version ? "selected" : ""}>v${v}</option>`)
      .join("");
    const rolesTabs = ["All", ...rolesList];
    const tabsHtml = rolesTabs
      .map(
        (role) =>
          `<div class="tab ${currentRole === role ? "active" : ""}" data-role="${role}">${role}</div>`
      )
      .join("");

    app.innerHTML = `
      <div class="card stack">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <h2>Результат анализа: ${counts.epicCount} эпика, ${counts.taskCount} задач</h2>
          <button class="btn secondary" id="altButton">Сгенерировать альтернативу</button>
        </div>
        <div class="tabs" id="roleTabs">${tabsHtml}</div>
        <div class="mode-toggle">
          <button class="btn secondary" id="summaryMode">Сводно</button>
          <button class="btn secondary" id="detailMode">Детально</button>
          <select id="versionSelect">${versionOptions}</select>
        </div>
        <div id="tableArea"></div>
        <div class="footer-actions">
          <button class="btn secondary" id="exportBtn">Экспорт в Jira</button>
          <button class="btn secondary" id="editBtn">Редактировать</button>
          <button class="btn" id="saveBtn">Сохранить</button>
        </div>
        <details id="errorDetails" style="display:none;">
          <summary>Show details</summary>
          <pre id="rawOutput"></pre>
        </details>
        <div class="status" id="saveStatus"></div>
      </div>
    `;

    document.getElementById("summaryMode").addEventListener("click", () => {
      displayMode = "summary";
      renderTable();
    });
    document.getElementById("detailMode").addEventListener("click", () => {
      displayMode = "detail";
      renderTable();
    });
    document.getElementById("exportBtn").addEventListener("click", () => {
      const version = currentResult.version;
      window.location.href = `${API_BASE}/documents/${documentId}/export/json?version=${version}`;
    });
    document.getElementById("editBtn").addEventListener("click", () => {
      editMode = !editMode;
      renderTable();
    });
    document.getElementById("saveBtn").addEventListener("click", async () => {
      recalcTotals();
      const payload = { result_json: currentResult };
      const saved = await fetchJson(`${API_BASE}/documents/${documentId}/result/version`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      currentResult.version = saved.version;
      document.getElementById("saveStatus").textContent = `Сохранено. Версия ${saved.version}.`;
      renderResult();
    });

    document.getElementById("altButton").addEventListener("click", async () => {
      const payload = {
        prompt: documentMeta.prompt,
        params: documentMeta.params,
      };
      const response = await fetchJson(
        `${API_BASE}/presales/${presaleId}/documents/alternative`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }
      );
      navigate(`/presales/${presaleId}/result?document_id=${response.document_id}`);
    });

    document.getElementById("versionSelect").addEventListener("change", async (event) => {
      const version = event.target.value;
      currentResult = await fetchJson(
        `${API_BASE}/documents/${documentId}/result?version=${version}`
      );
      renderResult();
    });

    document.getElementById("roleTabs").addEventListener("click", (event) => {
      const role = event.target.dataset.role;
      if (!role) return;
      currentRole = role;
      renderResult();
    });

    renderTable();

    const errorDetails = document.getElementById("errorDetails");
    const rawOutput = document.getElementById("rawOutput");
    if (currentResult.raw_llm_output) {
      errorDetails.style.display = "block";
      rawOutput.textContent = currentResult.raw_llm_output;
    } else {
      errorDetails.style.display = "none";
      rawOutput.textContent = "";
    }
  };

  const renderTable = () => {
    const tableArea = document.getElementById("tableArea");
    if (!tableArea || !currentResult) return;
    if (displayMode === "summary") {
      const rows = buildSummaryRows(currentResult).filter(
        (row) => currentRole === "All" || row.role === currentRole
      );
      tableArea.innerHTML = `
        <table>
          <thead>
            <tr>
              <th>Role</th>
              <th>Optimistic</th>
              <th>Most Likely</th>
              <th>Pessimistic</th>
              <th>Expected</th>
            </tr>
          </thead>
          <tbody>
            ${rows
              .map(
                (row) => `
                  <tr>
                    <td>${row.role}</td>
                    <td>${row.optimistic.toFixed(1)}</td>
                    <td>${row.most_likely.toFixed(1)}</td>
                    <td>${row.pessimistic.toFixed(1)}</td>
                    <td>${row.expected.toFixed(1)}</td>
                  </tr>
                `
              )
              .join("")}
          </tbody>
        </table>
      `;
      return;
    }
    const rows = [];
    let globalIndex = 0;
    (currentResult.epics || []).forEach((epic) => {
      (epic.tasks || []).forEach((task) => {
        rows.push({ epic: epic.name, task, index: globalIndex });
        globalIndex += 1;
      });
    });
    const filteredRows = currentRole === "All"
      ? rows
      : rows.filter((row) => row.task.role === currentRole);
    tableArea.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>Epic</th>
            <th>Task</th>
            <th>Role</th>
            <th>Optimistic</th>
            <th>Most Likely</th>
            <th>Pessimistic</th>
            <th>Expected</th>
          </tr>
        </thead>
        <tbody>
          ${filteredRows
            .map((row) => {
              const pert = row.task.pert_hours || {};
              const optimistic = pert.optimistic ?? 0;
              const mostLikely = pert.most_likely ?? 0;
              const pessimistic = pert.pessimistic ?? 0;
              const expected = pert.expected ?? 0;
              if (editMode) {
                return `
                  <tr>
                    <td>${row.epic || ""}</td>
                    <td>${row.task.name || ""}</td>
                    <td>${row.task.role || ""}</td>
                    <td><input type="number" data-index="${row.index}" data-field="optimistic" value="${optimistic}" /></td>
                    <td><input type="number" data-index="${row.index}" data-field="most_likely" value="${mostLikely}" /></td>
                    <td><input type="number" data-index="${row.index}" data-field="pessimistic" value="${pessimistic}" /></td>
                    <td>${expected}</td>
                  </tr>
                `;
              }
              return `
                <tr>
                  <td>${row.epic || ""}</td>
                  <td>${row.task.name || ""}</td>
                  <td>${row.task.role || ""}</td>
                  <td>${optimistic}</td>
                  <td>${mostLikely}</td>
                  <td>${pessimistic}</td>
                  <td>${expected}</td>
                </tr>
              `;
            })
            .join("")}
        </tbody>
      </table>
    `;

    if (editMode) {
      tableArea.querySelectorAll("input").forEach((input) => {
        input.addEventListener("input", (event) => {
          const index = Number(event.target.dataset.index);
          const field = event.target.dataset.field;
          const rowsAll = [];
          (currentResult.epics || []).forEach((epic) => {
            (epic.tasks || []).forEach((task) => {
              rowsAll.push(task);
            });
          });
          const task = rowsAll[index];
          task.pert_hours = task.pert_hours || {};
          task.pert_hours[field] = Number(event.target.value);
          recalcExpected(task);
          recalcTotals();
        });
      });
    }
  };

  const recalcTotals = () => {
    let total = 0;
    (currentResult.epics || []).forEach((epic) => {
      (epic.tasks || []).forEach((task) => {
        recalcExpected(task);
        total += Number(task.pert_hours?.expected || 0);
      });
    });
    currentResult.totals = { expected_hours: Number(total.toFixed(2)) };
  };

  await loadResult();
}

route();
