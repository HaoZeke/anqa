--- Live Groket sessions over the TUI control socket (Neovim 0.9+).
--- Requires a running ``groket`` TUI with the control socket enabled.

local M = {}

local uv = vim.uv or vim.loop

---@class groket.Config
---@field socket string|nil absolute path, or nil for runtime default
---@field executable string groket CLI for optional TUI start
---@field timeout_ms integer request wait
---@field auto_start boolean start TUI when socket missing and session is a directory
---@field keys table<string, string|false>|nil global maps; set false to disable
---@field picker "auto"|"select"|"telescope"|"fzf-lua"|"mini"|"snacks"|nil

M.config = {
  socket = nil,
  executable = "groket",
  timeout_ms = 10000,
  -- Detached Textual without a PTY is unreliable; prefer an already-running TUI.
  auto_start = false,
  -- Markdown projection only (HTML comment anchors). Org remains the Emacs client.
  format = "markdown",
  -- auto: telescope/fzf-lua/mini/snacks when installed, else vim.ui.select
  picker = "auto",
  keys = {
    find = "<leader>gs", -- pick session (ui.select / ecosystem picker)
    list = "<leader>gl", -- session browser buffer
    refresh = "<leader>gR", -- refresh open projection
    connect = "<leader>gc",
  },
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
    if tostring(err_msg):find("method not found", 1, true) then
      error(
        err_msg
          .. " — restart the groket TUI from a build that includes this control method ("
          .. method
          .. ")"
      )
    end
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
          local idx = tostring(params.promptIndex)
          local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
          for i, line in ipairs(lines) do
            -- Boundary after the index so "4" does not match "prompt-index=41".
            local hit = line:find("prompt%-index=" .. idx .. "%f[%D]", 1, false)
              or line == ("## Prompt " .. idx)
              or line == (":GROKET_PROMPT_INDEX: " .. idx)
            if hit then
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

---Parse ``<!-- groket:k=v k2=v2 -->`` machine tags (Markdown projection).
---Only column-0 comments count; indented transcript content must not match.
---@param line string
---@return table<string, string>|nil
local function parse_groket_comment(line)
  local inner = line:match("^<!%-%-%s*groket:([^>]-)%s*%-%->%s*$")
  if not inner then
    return nil
  end
  local meta = {}
  for token in inner:gmatch("%S+") do
    local key, value = token:match("^([%w%-_]+)=(.*)$")
    if key then
      meta[key] = value
    end
  end
  return meta
end

---Strip the 4-space field/transcript indent from a rendered Markdown body line.
---@param line string
---@return string
local function strip_md_fixed_line(line)
  if line:sub(1, 4) == "    " then
    return line:sub(5)
  end
  return line
end

---Scan upward for a groket HTML comment attribute.
---@param lines string[]
---@param row integer 1-based
---@param name string
---@return string|nil
local function ancestor_meta(lines, row, name)
  for i = row, 1, -1 do
    local meta = parse_groket_comment(lines[i])
    if meta and meta[name] then
      return meta[name]
    end
  end
  return nil
end

---Resolve a machine attribute at *row*.
---Tags sit on the line under headings (``#### note`` then ``<!-- groket:note-id=… -->``),
---so a cursor on the heading must look a short distance *down* as well as up.
---@param lines string[]
---@param row integer 1-based
---@param name string
---@return string|nil
local function meta_near_row(lines, row, name)
  local found = ancestor_meta(lines, row, name)
  if found then
    return found
  end
  -- Immediate look-ahead only (heading → optional blank → tag); avoid stealing a
  -- later note from transcript lines above ``### Operator notes``.
  for i = row + 1, math.min(row + 2, #lines) do
    local meta = parse_groket_comment(lines[i])
    if meta and meta[name] then
      return meta[name]
    end
  end
  return nil
end

---Markdown AT heading level (``#`` count), or nil.
---@param line string
---@return integer|nil
local function md_heading_level(line)
  local hashes = line:match("^(#+)%s+")
  if not hashes then
    return nil
  end
  return #hashes
end

---Locate #### note heading span for *note_id*.
---@param lines string[]
---@param note_id string
---@return integer, integer, integer
local function note_span_md(lines, note_id)
  local tag_line
  for i, line in ipairs(lines) do
    local meta = parse_groket_comment(line)
    if meta and meta["note-id"] == note_id and not meta["field-id"] then
      tag_line = i
      break
    end
  end
  if not tag_line then
    error("could not locate note " .. note_id)
  end
  local note_start
  for i = tag_line, 1, -1 do
    if md_heading_level(lines[i]) then
      note_start = i
      break
    end
  end
  if not note_start then
    error("could not locate note heading for " .. note_id)
  end
  local note_level = md_heading_level(lines[note_start])
  local note_end = #lines
  for i = note_start + 1, #lines do
    local level = md_heading_level(lines[i])
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
  local note_id = meta_near_row(lines, row, "note-id")
  if not note_id then
    error("cursor is not inside an operator note")
  end
  local note_start, note_end, note_level = note_span_md(lines, note_id)
  -- turn-index lives on the prompt tag above the note heading.
  local turn_index = tonumber(ancestor_meta(lines, note_start, "turn-index") or "0") or 0
  local event_text = ""
  local created_at = ""
  for i = note_start, math.min(note_start + 5, note_end) do
    local meta = parse_groket_comment(lines[i])
    if meta and meta["note-id"] == note_id and not meta["field-id"] then
      event_text = meta["event-indices"] or ""
      created_at = meta.created or ""
      break
    end
  end
  local fields = vim.empty_dict()
  local i = note_start
  while i <= note_end do
    local level = md_heading_level(lines[i])
    if level and level == note_level + 1 then
      local field_id
      local body_start = i + 1
      -- optional HTML comment immediately under heading
      while body_start <= note_end and lines[body_start]:match("^%s*$") do
        body_start = body_start + 1
      end
      if body_start <= note_end then
        local meta = parse_groket_comment(lines[body_start])
        if meta and meta["field-id"] then
          field_id = meta["field-id"]
          body_start = body_start + 1
          while body_start <= note_end and lines[body_start]:match("^%s*$") do
            body_start = body_start + 1
          end
        end
      end
      if field_id then
        local body_end = note_end
        for k = body_start, note_end do
          local lvl = md_heading_level(lines[k])
          if lvl and lvl <= level then
            body_end = k - 1
            break
          end
        end
        local body = {}
        for k = body_start, body_end do
          table.insert(body, strip_md_fixed_line(lines[k]))
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

---Exposed for headless round-trip tests (``nvim --headless -l``).
M._note_at_row = note_at_row
M._parse_groket_comment = parse_groket_comment

local function prompt_index_at_row(buf, row)
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local raw = meta_near_row(lines, row, "prompt-index")
  return raw and tonumber(raw) or nil
end

local function turn_index_at_row(buf, row)
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local raw = meta_near_row(lines, row, "turn-index")
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
  local ft = (M.config.format == "org") and "org" or "markdown"
  pcall(function()
    vim.bo[buf].filetype = ft
  end)
  vim.bo[buf].modified = false
  pcall(vim.api.nvim_buf_set_name, buf, "groket://" .. session_id)
  map_buffer(buf)
end

---Move the cursor onto a note after create/render (first field body, else heading).
---@param buf integer
---@param note_id string
local function jump_to_note(buf, note_id)
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local note_heading_row ---@type integer|nil
  local first_field_body ---@type integer|nil
  for i, line in ipairs(lines) do
    local meta = parse_groket_comment(line)
    if meta and meta["note-id"] == note_id and not meta["field-id"] then
      for h = i, 1, -1 do
        if md_heading_level(lines[h]) then
          note_heading_row = h
          break
        end
      end
    elseif meta and meta["note-id"] == note_id and meta["field-id"] and not first_field_body then
      local body = i + 1
      while body <= #lines and lines[body]:match("^%s*$") do
        body = body + 1
      end
      -- Empty field: still land on the blank line under the field tag.
      first_field_body = math.min(body, #lines)
    end
  end
  local target = first_field_body or note_heading_row
  if not target then
    return
  end
  local win = vim.fn.bufwinid(buf)
  if win ~= -1 then
    pcall(vim.api.nvim_win_set_cursor, win, { target, 0 })
  end
end

M._jump_to_note = jump_to_note

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
  local status = entry.status or "—"
  local origin = entry.origin or ""
  local model = entry.model or ""
  -- Compact, scannable: [status] origin · title · model · id
  local bits = {
    string.format("%-8s", status),
    (origin ~= "" and origin) or "work",
    head,
  }
  if model ~= "" then
    table.insert(bits, model)
  end
  if session_id ~= "" and session_id ~= head then
    table.insert(bits, session_id)
  end
  return table.concat(bits, "  ·  ")
end

local function session_entry_path(entry)
  return entry.path or entry.sessionId
end

local function session_entry_search_text(entry)
  return table.concat({
    entry.sessionId or "",
    entry.path or "",
    entry.title or "",
    entry.label or "",
    entry.model or "",
    entry.status or "",
    entry.outcome or "",
    entry.origin or "",
  }, " "):lower()
end

---@class groket.PickItem
---@field label string
---@field path string
---@field entry table
---@field search string

---@param sessions table[]
---@return groket.PickItem[]
local function sessions_to_items(sessions)
  local items = {}
  local seen = {}
  for _, entry in ipairs(sessions) do
    local label = session_entry_label(entry)
    local key = label
    local n = 2
    while seen[key] do
      key = label .. " (" .. tostring(n) .. ")"
      n = n + 1
    end
    seen[key] = true
    table.insert(items, {
      label = key,
      path = session_entry_path(entry),
      entry = entry,
      search = session_entry_search_text(entry),
    })
  end
  return items
end

local function fuzzy_score(haystack, needle)
  if needle == nil or needle == "" then
    return 1
  end
  needle = needle:lower()
  haystack = haystack:lower()
  if haystack:find(needle, 1, true) then
    return 1000 - (haystack:find(needle, 1, true) or 0)
  end
  -- subsequence match (fzf-ish)
  local hi = 1
  local score = 0
  local last = 0
  for i = 1, #needle do
    local ch = needle:sub(i, i)
    local found = haystack:find(ch, hi, true)
    if not found then
      return nil
    end
    if last > 0 and found == last + 1 then
      score = score + 5
    else
      score = score + 1
    end
    last = found
    hi = found + 1
  end
  return score
end

---@param items groket.PickItem[]
---@param query string
---@return groket.PickItem[]
local function filter_items(items, query)
  query = vim.trim(query or "")
  if query == "" then
    return items
  end
  local scored = {}
  for _, item in ipairs(items) do
    local s = fuzzy_score(item.search .. " " .. item.label, query)
    if s then
      table.insert(scored, { s = s, item = item })
    end
  end
  table.sort(scored, function(a, b)
    if a.s == b.s then
      return a.item.label < b.item.label
    end
    return a.s > b.s
  end)
  local out = {}
  for _, row in ipairs(scored) do
    table.insert(out, row.item)
  end
  return out
end

---Stock Neovim picker (and any `vim.ui.select` override, e.g. dressing / telescope-ui-select).
---@param items groket.PickItem[]
---@param on_choice fun(item: groket.PickItem|nil)
local function pick_with_ui_select(items, on_choice)
  local labels = {}
  local by = {}
  for _, item in ipairs(items) do
    table.insert(labels, item.label)
    by[item.label] = item
  end
  vim.ui.select(labels, {
    prompt = "Groket session",
    kind = "groket_session",
    format_item = function(label)
      return label
    end,
  }, function(choice)
    on_choice(choice and by[choice] or nil)
  end)
end

---@param items groket.PickItem[]
---@param on_choice fun(item: groket.PickItem|nil)
local function pick_with_telescope(items, on_choice)
  local pickers = require("telescope.pickers")
  local finders = require("telescope.finders")
  local conf = require("telescope.config").values
  local actions = require("telescope.actions")
  local action_state = require("telescope.actions.state")
  pickers
    .new({}, {
      prompt_title = "Groket sessions",
      finder = finders.new_table({
        results = items,
        entry_maker = function(item)
          return {
            value = item,
            display = item.label,
            ordinal = item.search .. " " .. item.label,
          }
        end,
      }),
      sorter = conf.generic_sorter({}),
      attach_mappings = function(prompt_bufnr, _)
        actions.select_default:replace(function()
          local selection = action_state.get_selected_entry()
          actions.close(prompt_bufnr)
          on_choice(selection and selection.value or nil)
        end)
        return true
      end,
    })
    :find()
end

---@param items groket.PickItem[]
---@param on_choice fun(item: groket.PickItem|nil)
local function pick_with_fzf_lua(items, on_choice)
  local fzf = require("fzf-lua")
  local labels = {}
  local by = {}
  for _, item in ipairs(items) do
    table.insert(labels, item.label)
    by[item.label] = item
  end
  fzf.fzf_exec(labels, {
    prompt = "Groket> ",
    actions = {
      ["default"] = function(selected)
        local label = selected and selected[1]
        on_choice(label and by[label] or nil)
      end,
    },
  })
end

---@param items groket.PickItem[]
---@param on_choice fun(item: groket.PickItem|nil)
local function pick_with_mini(items, on_choice)
  local mini_pick = require("mini.pick")
  local labels = vim.tbl_map(function(i)
    return i.label
  end, items)
  local by = {}
  for _, item in ipairs(items) do
    by[item.label] = item
  end
  mini_pick.start({
    source = {
      items = labels,
      name = "Groket sessions",
      choose = function(label)
        on_choice(label and by[label] or nil)
      end,
    },
  })
end

---@param items groket.PickItem[]
---@param on_choice fun(item: groket.PickItem|nil)
local function pick_with_snacks(items, on_choice)
  local snacks = require("snacks")
  local rows = {}
  for _, item in ipairs(items) do
    table.insert(rows, {
      text = item.label,
      item = item,
    })
  end
  snacks.picker.pick({
    items = rows,
    format = "text",
    confirm = function(picker, item)
      picker:close()
      on_choice(item and item.item or nil)
    end,
  })
end

---@param items groket.PickItem[]
---@param on_choice fun(item: groket.PickItem|nil)
local function pick_sessions(items, on_choice)
  local mode = M.config.picker or "auto"
  if mode == "select" or mode == "float" then
    -- "float" kept as an alias for older configs; stock select is the reliable UI.
    pick_with_ui_select(items, on_choice)
    return
  end
  local function try(name, fn)
    if mode ~= "auto" and mode ~= name then
      return false
    end
    local ok = pcall(fn)
    return ok
  end
  if
    try("snacks", function()
      pick_with_snacks(items, on_choice)
    end)
  then
    return
  end
  if
    try("telescope", function()
      pick_with_telescope(items, on_choice)
    end)
  then
    return
  end
  if
    try("fzf-lua", function()
      pick_with_fzf_lua(items, on_choice)
    end)
  then
    return
  end
  if
    try("mini", function()
      pick_with_mini(items, on_choice)
    end)
  then
    return
  end
  pick_with_ui_select(items, on_choice)
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

local function render_params(session)
  -- Neovim client only implements Markdown machine tags.
  return {
    session = session,
    format = "markdown",
  }
end

function M.open_session(session, prompt_index)
  run(function()
    local reference = normalize_session(session)
    ensure_connection(reference)
    local result = M.request("session/render", render_params(reference))
    local buf = vim.api.nvim_create_buf(true, true)
    apply_document(buf, result.text, result.sessionId, result.notesRevision, reference)
    local params = { session = reference }
    if prompt_index ~= nil then
      params.promptIndex = prompt_index
    end
    M.request("session/open", params)
    vim.api.nvim_set_current_buf(buf)
    notify("opened " .. result.sessionId .. " (" .. (result.format or M.config.format) .. ")")
  end)
end

---Pick a catalog session and open it (``vim.ui.select`` or ecosystem picker).
---@param query string|nil optional pre-filter sent to the server
function M.find_session(query)
  run(function()
    -- Pull full catalog when query empty so the picker can filter locally;
    -- server pre-filter still applies when the user passes a seed query.
    local result = M.list_sessions(query, query and query ~= "" and 200 or 500)
    local sessions = result.sessions or {}
    if #sessions == 0 then
      local suffix = (query and query ~= "") and (" for " .. vim.inspect(query)) or ""
      error("no sessions matched" .. suffix .. " (is the TUI running with a catalog?)")
    end
    local items = sessions_to_items(sessions)
    vim.schedule(function()
      pick_sessions(items, function(item)
        if item and item.path then
          M.open_session(item.path)
        end
      end)
    end)
  end)
end

---Session browser buffer with live `/` filter and open keys.
---@param query string|nil
function M.show_sessions(query)
  run(function()
    local result = M.list_sessions(query, 500)
    local sessions = result.sessions or {}
    local items = sessions_to_items(sessions)
    vim.schedule(function()
      local buf = vim.api.nvim_create_buf(true, true)
      vim.bo[buf].buftype = "nofile"
      vim.bo[buf].bufhidden = "wipe"
      vim.bo[buf].swapfile = false
      vim.bo[buf].modifiable = true

      local function paint(filter)
        local filtered = filter_items(items, filter or "")
        local lines = {
          string.format(
            "Groket  %d/%d  f filter · <CR>/o open · p pick · r refresh · q quit%s",
            #filtered,
            #items,
            (filter and filter ~= "") and ("  ·  /" .. filter) or ""
          ),
          "",
        }
        local paths = {}
        if #filtered == 0 then
          table.insert(lines, "(no matches)")
        else
          for i, item in ipairs(filtered) do
            table.insert(lines, item.label)
            paths[i + 2] = item.path
          end
        end
        vim.bo[buf].modifiable = true
        vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
        vim.bo[buf].modifiable = false
        vim.b[buf].groket_session_paths = paths
        vim.b[buf].groket_session_filter = filter or ""
      end

      paint(query or "")
      vim.bo[buf].filetype = "groket-sessions"
      pcall(vim.api.nvim_buf_set_name, buf, "groket://sessions")

      local function open_row()
        local row = vim.api.nvim_win_get_cursor(0)[1]
        local path = vim.b[buf].groket_session_paths and vim.b[buf].groket_session_paths[row]
        if path then
          M.open_session(path)
        else
          notify("no session on this line", vim.log.levels.WARN)
        end
      end

      vim.keymap.set("n", "<CR>", open_row, { buffer = buf, desc = "Groket: open session" })
      vim.keymap.set("n", "o", open_row, { buffer = buf, desc = "Groket: open session" })
      vim.keymap.set("n", "q", "<cmd>bd!<cr>", { buffer = buf, desc = "Groket: close browser" })
      vim.keymap.set("n", "r", function()
        M.show_sessions(vim.b[buf].groket_session_filter)
      end, { buffer = buf, desc = "Groket: reload catalog" })
      vim.keymap.set("n", "f", function()
        vim.ui.input({
          prompt = "Filter sessions: ",
          default = vim.b[buf].groket_session_filter or "",
        }, function(input)
          if input ~= nil then
            paint(input)
          end
        end)
      end, { buffer = buf, desc = "Groket: filter list" })
      vim.keymap.set("n", "/", function()
        vim.ui.input({
          prompt = "Filter sessions: ",
          default = vim.b[buf].groket_session_filter or "",
        }, function(input)
          if input ~= nil then
            paint(input)
          end
        end)
      end, { buffer = buf, desc = "Groket: filter list" })
      -- Avoid bare ``g`` (shadows ``gg``); session buffers document the same rule.
      vim.keymap.set("n", "p", function()
        M.find_session(vim.b[buf].groket_session_filter)
      end, { buffer = buf, desc = "Groket: pick session" })

      vim.api.nvim_set_current_buf(buf)
      notify(string.format("listed %d session(s) — <CR> open · f filter · p pick", #items))
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
    local result = M.request("session/render", render_params(reference))
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
    local rendered = M.request("session/render", render_params(reference))
    apply_document(buf, rendered.text, rendered.sessionId, rendered.notesRevision, reference)
    jump_to_note(buf, note_id)
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
    local note_id = meta_near_row(lines, row, "note-id")
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
    local rendered = M.request("session/render", render_params(reference))
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
    local seen = {}
    for i, line in ipairs(lines) do
      local meta = parse_groket_comment(line)
      if meta and meta["note-id"] and not meta["field-id"] and not seen[meta["note-id"]] then
        seen[meta["note-id"]] = true
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

local function bind_global_keys()
  local keys = M.config.keys
  if type(keys) ~= "table" then
    return
  end
  local maps = {
    find = { rhs = function()
      M.find_session()
    end, desc = "Groket: fuzzy find session" },
    list = { rhs = function()
      M.show_sessions()
    end, desc = "Groket: session browser" },
    refresh = { rhs = function()
      M.refresh()
    end, desc = "Groket: refresh session buffer" },
    connect = { rhs = function()
      run(function()
        M.connect()
        notify("connected to " .. default_socket_path())
      end)
    end, desc = "Groket: connect control socket" },
  }
  for name, spec in pairs(maps) do
    local lhs = keys[name]
    if type(lhs) == "string" and lhs ~= "" then
      vim.keymap.set("n", lhs, spec.rhs, { silent = true, desc = spec.desc })
    end
  end
end

function M.setup(opts)
  M.config = vim.tbl_deep_extend("force", M.config, opts or {})
  vim.g.groket_setup_done = true
  if vim.g.groket_commands_registered then
    bind_global_keys()
    return
  end
  vim.g.groket_commands_registered = true
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
  end, {})
  vim.api.nvim_create_user_command("GroketSaveNote", function()
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
  bind_global_keys()
end

return M
