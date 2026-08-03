--- Live Groket sessions over the TUI control socket (Neovim 0.9+).
--- Requires a running ``groket`` TUI with the control socket enabled.

local M = {}

local uv = vim.uv or vim.loop

---@class groket.Config
---@field socket string|nil absolute path, or nil for runtime default
---@field executable string groket CLI for optional TUI start
---@field timeout_ms integer request wait
---@field auto_start boolean start TUI when socket missing and session is a directory

M.config = {
  socket = nil,
  executable = "groket",
  timeout_ms = 10000,
  auto_start = true,
}

local state = {
  sock = nil, ---@type uv.uv_pipe_t|nil
  read_buf = "",
  next_id = 1,
  pending = {}, ---@type table<integer, {resolve: fun(any), reject: fun(string)}>
  started_job = nil, ---@type integer|nil
}

local function default_socket_path()
  if M.config.socket and M.config.socket ~= "" then
    return vim.fn.expand(M.config.socket)
  end
  local runtime = vim.env.XDG_RUNTIME_DIR
  if runtime and runtime ~= "" then
    return runtime .. "/groket/control.sock"
  end
  return vim.fn.expand("~/.groket/run/control.sock")
end

local function notify(msg, level)
  vim.schedule(function()
    vim.notify(msg, level or vim.log.levels.INFO, { title = "groket" })
  end)
end

local function decode_line(line)
  local ok, obj = pcall(vim.json.decode, line)
  if ok then
    return obj
  end
  return nil
end

local function resume_waiter(id, fn)
  local waiter = state.pending[id]
  if not waiter then
    return
  end
  state.pending[id] = nil
  vim.schedule(function()
    fn(waiter)
  end)
end

local function handle_message(msg)
  if msg.id ~= nil then
    resume_waiter(msg.id, function(waiter)
      if msg.error then
        local detail = msg.error.message or "request failed"
        local data = msg.error.data
        if type(data) == "table" and data.currentRevision then
          detail = detail .. " (revision " .. tostring(data.currentRevision) .. ")"
        end
        waiter.reject(detail)
      else
        waiter.resolve(msg.result)
      end
    end)
    return
  end
  if msg.method then
    vim.schedule(function()
      M._on_notification(msg.method, msg.params or {})
    end)
  end
end

local function on_read(err, data)
  if err then
    notify("control read error: " .. tostring(err), vim.log.levels.ERROR)
    vim.schedule(function()
      M.disconnect()
    end)
    return
  end
  if data == nil then
    vim.schedule(function()
      M.disconnect()
    end)
    return
  end
  state.read_buf = state.read_buf .. data
  while true do
    local nl = state.read_buf:find("\n", 1, true)
    if not nl then
      break
    end
    local line = state.read_buf:sub(1, nl - 1)
    state.read_buf = state.read_buf:sub(nl + 1)
    if line:match("%S") then
      local msg = decode_line(line)
      if msg then
        handle_message(msg)
      end
    end
  end
end

function M.disconnect()
  for id, waiter in pairs(state.pending) do
    state.pending[id] = nil
    vim.schedule(function()
      waiter.reject("disconnected")
    end)
  end
  if state.sock then
    pcall(function()
      state.sock:read_stop()
      state.sock:close()
    end)
    state.sock = nil
  end
  state.read_buf = ""
end

function M.connected()
  return state.sock ~= nil and not state.sock:is_closing()
end

---@param method string
---@param params table|nil
---@return any
function M.request(method, params)
  if not M.connected() then
    error("not connected to groket control socket")
  end
  local co = coroutine.running()
  if not co then
    error("groket.request must run inside a coroutine")
  end
  local id = state.next_id
  state.next_id = id + 1
  local payload = vim.json.encode({
    jsonrpc = "2.0",
    id = id,
    method = method,
    params = params or vim.empty_dict(),
  })
  local result, err_msg
  local settled = false
  state.pending[id] = {
    resolve = function(value)
      if settled then
        return
      end
      settled = true
      result = value
      coroutine.resume(co)
    end,
    reject = function(message)
      if settled then
        return
      end
      settled = true
      err_msg = message
      coroutine.resume(co)
    end,
  }
  state.sock:write(payload .. "\n", function(write_err)
    if write_err then
      resume_waiter(id, function(waiter)
        waiter.reject("write failed: " .. tostring(write_err))
      end)
    end
  end)
  vim.defer_fn(function()
    if state.pending[id] then
      resume_waiter(id, function(waiter)
        waiter.reject("timeout waiting for " .. method)
      end)
    end
  end, M.config.timeout_ms)
  coroutine.yield()
  if err_msg then
    error(err_msg)
  end
  return result
end

local function wait_for_socket(timeout_ms)
  local deadline = uv.now() + (timeout_ms or M.config.timeout_ms)
  while uv.now() < deadline do
    if uv.fs_stat(default_socket_path()) then
      return true
    end
    vim.wait(50)
  end
  return uv.fs_stat(default_socket_path()) ~= nil
end

function M.start_tui(session)
  local sock = default_socket_path()
  local args = { M.config.executable, "--control-socket", sock }
  if session and session ~= "" then
    args = {
      M.config.executable,
      "--path",
      vim.fn.fnamemodify(session, ":p"),
      "--control-socket",
      sock,
    }
  end
  state.started_job = vim.fn.jobstart(args, { detach = true })
  if state.started_job <= 0 then
    error("failed to start " .. M.config.executable)
  end
  if not wait_for_socket() then
    error("groket did not create control socket: " .. sock)
  end
end

function M.connect()
  if M.connected() then
    return
  end
  local path = default_socket_path()
  if not uv.fs_stat(path) then
    error("groket control socket does not exist: " .. path)
  end
  local co = coroutine.running()
  if not co then
    error("groket.connect must run inside a coroutine")
  end
  local sock = uv.new_pipe(false)
  local connect_err
  sock:connect(path, function(err)
    connect_err = err
    vim.schedule(function()
      coroutine.resume(co)
    end)
  end)
  coroutine.yield()
  if connect_err then
    sock:close()
    error("connect failed: " .. tostring(connect_err))
  end
  state.sock = sock
  sock:read_start(on_read)
  local version = vim.version()
  M.request("initialize", {
    protocolVersion = 1,
    clientInfo = {
      name = "Neovim",
      version = string.format("%d.%d.%d", version.major, version.minor, version.patch),
    },
  })
end

local function ensure_connection(session)
  if M.connected() then
    return
  end
  local path = default_socket_path()
  if not uv.fs_stat(path) then
    if M.config.auto_start and session and vim.fn.isdirectory(session) == 1 then
      M.start_tui(session)
    else
      error("groket control socket does not exist: " .. path .. " (start the TUI first)")
    end
  end
  M.connect()
end

local function normalize_session(session)
  if session == nil or session == "" then
    error("session path or id required")
  end
  if vim.fn.isdirectory(session) == 1 then
    return vim.fn.fnamemodify(session, ":p")
  end
  return session
end

---@param buf integer
local function set_stale_status(buf)
  local notes = vim.b[buf].groket_notes_stale and " Notes changed" or ""
  local trace = vim.b[buf].groket_session_stale and " Trace changed" or ""
  vim.b[buf].groket_status = vim.trim(trace .. notes)
end

function M._on_notification(method, params)
  local session = params.sessionId
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_loaded(buf) and vim.b[buf].groket_session_id then
      if session == nil or session == vim.b[buf].groket_session_id then
        if method == "notes/changed" then
          vim.b[buf].groket_notes_stale = true
        elseif method == "session/changed" then
          vim.b[buf].groket_session_stale = true
        elseif method == "session/selected" and params.promptIndex ~= nil then
          local needle = ":GROKET_PROMPT_INDEX: " .. tostring(params.promptIndex)
          local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
          for i, line in ipairs(lines) do
            if line == needle then
              local win = vim.fn.bufwinid(buf)
              if win ~= -1 then
                vim.api.nvim_win_set_cursor(win, { i, 0 })
              end
              break
            end
          end
        end
        set_stale_status(buf)
      end
    end
  end
end

local function property_value(line)
  local key, value = line:match("^:([%w_]+):%s*(.*)$")
  if key then
    return key, vim.trim(value or "")
  end
  return nil, nil
end

---Scan upward for an Org property drawer value.
---@param lines string[]
---@param row integer 1-based
---@param name string
---@return string|nil
local function ancestor_property(lines, row, name)
  local i = row
  while i >= 1 do
    if lines[i]:match("^:PROPERTIES:") then
      local j = i + 1
      while j <= #lines and not lines[j]:match("^:END:") do
        local key, value = property_value(lines[j])
        if key == name then
          return value
        end
        j = j + 1
      end
    end
    i = i - 1
  end
  return nil
end

local function heading_level(line)
  local stars = line:match("^(%*+)%s")
  if not stars then
    return nil
  end
  return #stars
end

---Locate the *** note heading for *note_id* and return start/end line indexes.
---@param lines string[]
---@param note_id string
---@return integer, integer, integer  start, end, level
local function note_span(lines, note_id)
  local prop_line
  for i, line in ipairs(lines) do
    local key, value = property_value(line)
    if key == "GROKET_NOTE_ID" and value == note_id then
      prop_line = i
      break
    end
  end
  if not prop_line then
    error("could not locate note " .. note_id)
  end
  local note_start
  for i = prop_line, 1, -1 do
    local level = heading_level(lines[i])
    if level then
      note_start = i
      break
    end
  end
  if not note_start then
    error("could not locate note heading for " .. note_id)
  end
  local note_level = heading_level(lines[note_start])
  local note_end = #lines
  for i = note_start + 1, #lines do
    local level = heading_level(lines[i])
    if level and level <= note_level then
      note_end = i - 1
      break
    end
  end
  return note_start, note_end, note_level
end

---@param buf integer
---@param row integer 1-based cursor line
---@return table
local function note_at_row(buf, row)
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local note_id = ancestor_property(lines, row, "GROKET_NOTE_ID")
  if not note_id then
    error("cursor is not inside an operator note")
  end
  local note_start, note_end, note_level = note_span(lines, note_id)
  local turn_index = tonumber(ancestor_property(lines, note_start + 1, "GROKET_TURN_INDEX") or "0")
    or 0
  local event_text = ancestor_property(lines, note_start + 1, "GROKET_EVENT_INDICES") or ""
  local created_at = ancestor_property(lines, note_start + 1, "GROKET_CREATED_AT") or ""
  local fields = vim.empty_dict()
  local i = note_start
  while i <= note_end do
    local level = heading_level(lines[i])
    if level and level == note_level + 1 then
      local field_id
      local body_start = i + 1
      if lines[body_start] and lines[body_start]:match("^:PROPERTIES:") then
        local j = body_start + 1
        while j <= note_end and not lines[j]:match("^:END:") do
          local key, value = property_value(lines[j])
          if key == "GROKET_FIELD_ID" then
            field_id = value
          end
          j = j + 1
        end
        body_start = j + 1
      end
      if field_id then
        local body_end = note_end
        for k = body_start, note_end do
          local lvl = heading_level(lines[k])
          if lvl and lvl <= level then
            body_end = k - 1
            break
          end
        end
        local body = {}
        for k = body_start, body_end do
          table.insert(body, lines[k])
        end
        while #body > 0 and body[#body] == "" do
          table.remove(body)
        end
        fields[field_id] = table.concat(body, "\n")
        i = body_end + 1
      else
        i = i + 1
      end
    else
      i = i + 1
    end
  end
  local event_indices = {}
  for part in string.gmatch(event_text, "[^,]+") do
    local n = tonumber(vim.trim(part))
    if n then
      table.insert(event_indices, n)
    end
  end
  return {
    id = note_id,
    turnIndex = turn_index,
    fields = fields,
    eventIndices = event_indices,
    createdAt = created_at,
    updatedAt = os.date("!%Y-%m-%dT%H:%M:%S+00:00"),
  }
end

local function prompt_index_at_row(buf, row)
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local raw = ancestor_property(lines, row, "GROKET_PROMPT_INDEX")
  return raw and tonumber(raw) or nil
end

local function turn_index_at_row(buf, row)
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local raw = ancestor_property(lines, row, "GROKET_TURN_INDEX")
  return raw and tonumber(raw) or nil
end

local function map_buffer(buf)
  local function map(lhs, rhs, desc)
    vim.keymap.set("n", lhs, rhs, { buffer = buf, silent = true, desc = desc })
  end
  -- Avoid bare ``g`` (breaks gg/gq/…). ``R`` reloads the projection.
  map("R", function()
    M.refresh()
  end, "Groket: refresh session")
  map("<LocalLeader>o", function()
    M.open_prompt_at_cursor()
  end, "Groket: select prompt in TUI")
  map("<LocalLeader>c", function()
    M.save_note()
  end, "Groket: save note at cursor")
  map("<LocalLeader>n", function()
    M.new_note()
  end, "Groket: new note")
  map("<LocalLeader>k", function()
    M.delete_note()
  end, "Groket: delete note")
  map("<LocalLeader>s", function()
    M.save_all_notes()
  end, "Groket: save all notes")
end

local function apply_document(buf, text, session_id, revision, reference)
  local lines = vim.split(text, "\n", { plain = true })
  if #lines > 0 and lines[#lines] == "" then
    table.remove(lines)
  end
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "hide"
  vim.bo[buf].swapfile = false
  vim.bo[buf].modifiable = true
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.b[buf].groket_session_id = session_id
  vim.b[buf].groket_session_reference = reference
  vim.b[buf].groket_notes_revision = revision
  vim.b[buf].groket_notes_stale = false
  vim.b[buf].groket_session_stale = false
  set_stale_status(buf)
  pcall(function()
    vim.bo[buf].filetype = "org"
  end)
  vim.bo[buf].modified = false
  pcall(vim.api.nvim_buf_set_name, buf, "groket://" .. session_id)
  map_buffer(buf)
end

local function run(fn)
  local co = coroutine.create(function()
    local ok, err = pcall(fn)
    if not ok then
      notify(tostring(err), vim.log.levels.ERROR)
    end
  end)
  local ok, err = coroutine.resume(co)
  if not ok then
    notify(tostring(err), vim.log.levels.ERROR)
  end
end

local function session_entry_label(entry)
  local title = entry.title or entry.label or ""
  local session_id = entry.sessionId or ""
  local head = (title ~= "" and title) or session_id
  local parts = { head }
  if entry.status and entry.status ~= "" then
    table.insert(parts, entry.status)
  end
  if entry.model and entry.model ~= "" then
    table.insert(parts, entry.model)
  end
  if entry.origin and entry.origin ~= "" then
    table.insert(parts, entry.origin)
  end
  if session_id ~= "" and session_id ~= head then
    table.insert(parts, session_id)
  end
  return table.concat(parts, "  ·  ")
end

local function session_entry_path(entry)
  return entry.path or entry.sessionId
end

---List catalog sessions from the running TUI.
---@param query string|nil
---@param limit integer|nil
---@return table result
function M.list_sessions(query, limit)
  local co = coroutine.running()
  if not co then
    error("groket.list_sessions must run inside a coroutine")
  end
  ensure_connection(nil)
  local params = { query = query or "" }
  if limit ~= nil then
    params.limit = limit
  end
  return M.request("session/list", params)
end

function M.open_session(session, prompt_index)
  run(function()
    local reference = normalize_session(session)
    ensure_connection(reference)
    local result = M.request("session/render", { session = reference })
    local buf = vim.api.nvim_create_buf(true, true)
    apply_document(buf, result.text, result.sessionId, result.notesRevision, reference)
    local params = { session = reference }
    if prompt_index ~= nil then
      params.promptIndex = prompt_index
    end
    M.request("session/open", params)
    vim.api.nvim_set_current_buf(buf)
    notify("opened " .. result.sessionId)
  end)
end

---Pick a catalog session (optional server-side QUERY) and open it.
---@param query string|nil
function M.find_session(query)
  run(function()
    local result = M.list_sessions(query)
    local sessions = result.sessions or {}
    if #sessions == 0 then
      local suffix = (query and query ~= "") and (" for " .. vim.inspect(query)) or ""
      error("no sessions matched" .. suffix)
    end
    local labels = {}
    local by_label = {}
    for _, entry in ipairs(sessions) do
      local label = session_entry_label(entry)
      local key = label
      local n = 2
      while by_label[key] do
        key = label .. " (" .. tostring(n) .. ")"
        n = n + 1
      end
      by_label[key] = session_entry_path(entry)
      table.insert(labels, key)
    end
    vim.schedule(function()
      vim.ui.select(labels, { prompt = "Groket session" }, function(choice)
        if not choice then
          return
        end
        local path = by_label[choice]
        if path then
          M.open_session(path)
        end
      end)
    end)
  end)
end

---Show catalog rows in a scratch buffer; press Enter to open.
---@param query string|nil
function M.show_sessions(query)
  run(function()
    local result = M.list_sessions(query)
    local sessions = result.sessions or {}
    local lines = {
      string.format(
        "Groket sessions  matched %s / total %s%s",
        tostring(result.matched or #sessions),
        tostring(result.total or #sessions),
        (query and query ~= "") and ("  filter: " .. query) or ""
      ),
      "",
    }
    local paths = {}
    if #sessions == 0 then
      table.insert(lines, "(no sessions)")
    else
      for i, entry in ipairs(sessions) do
        local line = session_entry_label(entry)
        table.insert(lines, line)
        paths[i + 2] = session_entry_path(entry) -- account for header lines
      end
    end
    vim.schedule(function()
      local buf = vim.api.nvim_create_buf(true, true)
      vim.bo[buf].buftype = "nofile"
      vim.bo[buf].bufhidden = "wipe"
      vim.bo[buf].swapfile = false
      vim.bo[buf].modifiable = true
      vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
      vim.bo[buf].modifiable = false
      vim.bo[buf].filetype = "groket-sessions"
      pcall(vim.api.nvim_buf_set_name, buf, "groket://sessions")
      vim.b[buf].groket_session_paths = paths
      vim.keymap.set("n", "<CR>", function()
        local row = vim.api.nvim_win_get_cursor(0)[1]
        local path = vim.b[buf].groket_session_paths and vim.b[buf].groket_session_paths[row]
        if path then
          M.open_session(path)
        else
          notify("no session on this line", vim.log.levels.WARN)
        end
      end, { buffer = buf, desc = "Groket: open session" })
      vim.api.nvim_set_current_buf(buf)
      notify(string.format("listed %d session(s)", #sessions))
    end)
  end)
end

function M.refresh()
  run(function()
    local buf = vim.api.nvim_get_current_buf()
    local reference = vim.b[buf].groket_session_reference or vim.b[buf].groket_session_id
    if not reference then
      error("not a groket session buffer")
    end
    if vim.bo[buf].modified then
      local choice = vim.fn.confirm("Discard unsaved note edits?", "&Yes\n&No", 2)
      if choice ~= 1 then
        return
      end
    end
    ensure_connection(reference)
    local result = M.request("session/render", { session = reference })
    local row = vim.api.nvim_win_get_cursor(0)[1]
    apply_document(buf, result.text, result.sessionId, result.notesRevision, reference)
    local line_count = vim.api.nvim_buf_line_count(buf)
    vim.api.nvim_win_set_cursor(0, { math.min(row, line_count), 0 })
  end)
end

function M.open_prompt_at_cursor()
  run(function()
    local buf = vim.api.nvim_get_current_buf()
    local reference = vim.b[buf].groket_session_reference
    if not reference then
      error("not a groket session buffer")
    end
    local row = vim.api.nvim_win_get_cursor(0)[1]
    local prompt_index = prompt_index_at_row(buf, row)
    if not prompt_index then
      error("cursor is not inside a prompt")
    end
    ensure_connection(reference)
    M.request("session/open", { session = reference, promptIndex = prompt_index })
    notify("selected prompt " .. tostring(prompt_index))
  end)
end

function M.save_note()
  run(function()
    local buf = vim.api.nvim_get_current_buf()
    local reference = vim.b[buf].groket_session_reference
    if not reference then
      error("not a groket session buffer")
    end
    local row = vim.api.nvim_win_get_cursor(0)[1]
    local note = note_at_row(buf, row)
    ensure_connection(reference)
    local result = M.request("notes/upsert", {
      session = reference,
      expectedRevision = vim.b[buf].groket_notes_revision,
      note = note,
    })
    vim.b[buf].groket_notes_revision = result.revision
    vim.b[buf].groket_notes_stale = false
    vim.bo[buf].modified = false
    set_stale_status(buf)
    notify("saved note " .. note.id)
  end)
end

function M.new_note()
  run(function()
    local buf = vim.api.nvim_get_current_buf()
    local reference = vim.b[buf].groket_session_reference
    if not reference then
      error("not a groket session buffer")
    end
    local row = vim.api.nvim_win_get_cursor(0)[1]
    local turn_index = turn_index_at_row(buf, row)
    local prompt_index = prompt_index_at_row(buf, row)
    if not turn_index or not prompt_index then
      error("cursor is not inside a prompt")
    end
    ensure_connection(reference)
    local listed = M.request("notes/list", { session = reference })
    local fields = vim.empty_dict()
    for _, spec in ipairs((listed.schema and listed.schema.fields) or {}) do
      if spec.id then
        fields[spec.id] = ""
      end
    end
    local timestamp = os.date("!%Y-%m-%dT%H:%M:%S+00:00")
    local note_id = "n-"
      .. vim.fn.sha256(tostring(uv.now()) .. tostring(math.random(1, 1000000000))):sub(1, 12)
    local result = M.request("notes/upsert", {
      session = reference,
      expectedRevision = vim.b[buf].groket_notes_revision,
      note = {
        id = note_id,
        turnIndex = turn_index,
        fields = fields,
        eventIndices = {},
        createdAt = timestamp,
        updatedAt = timestamp,
      },
    })
    vim.b[buf].groket_notes_revision = result.revision
    local rendered = M.request("session/render", { session = reference })
    apply_document(buf, rendered.text, rendered.sessionId, rendered.notesRevision, reference)
    notify("created note " .. note_id)
  end)
end

function M.delete_note()
  run(function()
    local buf = vim.api.nvim_get_current_buf()
    local reference = vim.b[buf].groket_session_reference
    if not reference then
      error("not a groket session buffer")
    end
    local row = vim.api.nvim_win_get_cursor(0)[1]
    local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
    local note_id = ancestor_property(lines, row, "GROKET_NOTE_ID")
    if not note_id then
      error("cursor is not inside an operator note")
    end
    local choice = vim.fn.confirm("Delete note " .. note_id .. "?", "&Yes\n&No", 2)
    if choice ~= 1 then
      return
    end
    ensure_connection(reference)
    local result = M.request("notes/delete", {
      session = reference,
      expectedRevision = vim.b[buf].groket_notes_revision,
      noteId = note_id,
    })
    vim.b[buf].groket_notes_revision = result.revision
    local rendered = M.request("session/render", { session = reference })
    apply_document(buf, rendered.text, rendered.sessionId, rendered.notesRevision, reference)
    notify("deleted note " .. note_id)
  end)
end

function M.save_all_notes()
  run(function()
    local buf = vim.api.nvim_get_current_buf()
    local reference = vim.b[buf].groket_session_reference
    if not reference then
      error("not a groket session buffer")
    end
    ensure_connection(reference)
    local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
    local rows = {}
    for i, line in ipairs(lines) do
      if line:match("^:GROKET_NOTE_ID:") then
        table.insert(rows, i)
      end
    end
    for _, row in ipairs(rows) do
      local note = note_at_row(buf, row)
      local result = M.request("notes/upsert", {
        session = reference,
        expectedRevision = vim.b[buf].groket_notes_revision,
        note = note,
      })
      vim.b[buf].groket_notes_revision = result.revision
    end
    vim.bo[buf].modified = false
    vim.b[buf].groket_notes_stale = false
    set_stale_status(buf)
    notify("saved " .. tostring(#rows) .. " note(s)")
  end)
end

function M.setup(opts)
  M.config = vim.tbl_deep_extend("force", M.config, opts or {})
  vim.api.nvim_create_user_command("GroketConnect", function()
    run(function()
      M.connect()
      notify("connected to " .. default_socket_path())
    end)
  end, {})
  vim.api.nvim_create_user_command("GroketDisconnect", function()
    M.disconnect()
    notify("disconnected")
  end, {})
  vim.api.nvim_create_user_command("GroketOpenSession", function(cmd)
    local args = vim.split(cmd.args, "%s+", { trimempty = true })
    if #args == 0 then
      notify("usage: :GroketOpenSession {session-path-or-id} [prompt-index]", vim.log.levels.ERROR)
      return
    end
    local prompt = args[2] and tonumber(args[2]) or nil
    M.open_session(args[1], prompt)
  end, { nargs = "+", complete = "dir" })
  vim.api.nvim_create_user_command("GroketSessions", function(cmd)
    local query = vim.trim(cmd.args or "")
    M.show_sessions(query ~= "" and query or nil)
  end, { nargs = "*" })
  vim.api.nvim_create_user_command("GroketFindSession", function(cmd)
    local query = vim.trim(cmd.args or "")
    M.find_session(query ~= "" and query or nil)
  end, { nargs = "*" })
  vim.api.nvim_create_user_command("GroketRefresh", function()
    M.refresh()
  end, {})  vim.api.nvim_create_user_command("GroketSaveNote", function()
    M.save_note()
  end, {})
  vim.api.nvim_create_user_command("GroketSaveAllNotes", function()
    M.save_all_notes()
  end, {})
  vim.api.nvim_create_user_command("GroketNewNote", function()
    M.new_note()
  end, {})
  vim.api.nvim_create_user_command("GroketDeleteNote", function()
    M.delete_note()
  end, {})
  vim.api.nvim_create_user_command("GroketOpenPrompt", function()
    M.open_prompt_at_cursor()
  end, {})
end

return M
