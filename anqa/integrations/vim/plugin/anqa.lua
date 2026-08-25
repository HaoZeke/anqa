--- Auto-load Anqa Neovim commands when this runtimepath is on &rtp.
if vim.g.loaded_anqa then
  return
end
vim.g.loaded_anqa = true

if vim.fn.has("nvim-0.9") == 0 then
  vim.notify("anqa.vim requires Neovim 0.9+", vim.log.levels.ERROR, { title = "anqa" })
  return
end

-- Prefer an explicit require("anqa").setup({…}) from the user init when present.
-- Only auto-setup when the user has not already configured the client.
if not vim.g.anqa_setup_done then
  require("anqa").setup()
end
