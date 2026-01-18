const API_BASE = "http://localhost:8080/api/v1";

const documentsEl = document.getElementById("documents");
const tableContainer = document.getElementById("tableContainer");
const docMeta = document.getElementById("docMeta");

let currentDocumentId = null;

async function loadDocuments() {
  const response = await fetch(`${API_BASE}/documents`);
  const docs = await response.json();
  documentsEl.innerHTML = "";
  docs.forEach((doc) => {
    const item = document.createElement("div");
    item.className = "doc-item";
    item.textContent = `${doc.document_id} (${doc.status})`;
    item.addEventListener("click", () => selectDocument(doc.document_id, item));
    documentsEl.appendChild(item);
  });
}

async function selectDocument(documentId, element) {
  currentDocumentId = documentId;
  document.querySelectorAll(".doc-item").forEach((el) => el.classList.remove("active"));
  element.classList.add("active");

  const response = await fetch(`${API_BASE}/documents/${documentId}/result`);
  if (!response.ok) {
    docMeta.textContent = "Result not available yet.";
    tableContainer.innerHTML = "";
    return;
  }
  const payload = await response.json();
  docMeta.textContent = `Version ${payload.version}`;
  renderTable(payload);
}

function renderTable(result) {
  const epics = result.epics || [];
  const rows = [];
  epics.forEach((epic) => {
    const tasks = epic.tasks || [];
    tasks.forEach((task) => {
      rows.push({ epic: epic.name, task });
    });
  });

  const table = document.createElement("table");
  table.innerHTML = `
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
    <tbody></tbody>
  `;
  const tbody = table.querySelector("tbody");
  rows.forEach((row) => {
    const pert = row.task.pert_hours || {};
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.epic || ""}</td>
      <td>${row.task.name || ""}</td>
      <td>${row.task.role || ""}</td>
      <td>${pert.optimistic ?? 0}</td>
      <td>${pert.most_likely ?? 0}</td>
      <td>${pert.pessimistic ?? 0}</td>
      <td>${pert.expected ?? 0}</td>
    `;
    tbody.appendChild(tr);
  });
  tableContainer.innerHTML = "";
  tableContainer.appendChild(table);
}

loadDocuments();
