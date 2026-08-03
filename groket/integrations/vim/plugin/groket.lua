--- Auto-load Groket Neovim commands when this runtimepath is on &rtp.
if vim.g.loaded_groket then
  return
end
vim.g.loaded_groket = true

if vim.fn.has("nvim-0.9") == 0 then
  vim.notify("groket.vim requires Neovim 0.9+", vim.log.levels.ERROR, { title = "groket" })
  return
end

require("groket").setup()
