Add-Type -AssemblyName System.Security
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$credentialFile = Join-Path $env:LOCALAPPDATA 'WeiyanForumDeploy\credentials.dpapi'
try {
    $encrypted = [IO.File]::ReadAllBytes($credentialFile)
    $plain = [Security.Cryptography.ProtectedData]::Unprotect($encrypted, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    $credentials = [Text.Encoding]::UTF8.GetString($plain) | ConvertFrom-Json
} catch {
    [Windows.Forms.MessageBox]::Show('无法读取论坛管理账号。请使用部署论坛时的电脑和 Windows 账号。','未言诗集') > $null
    exit 1
}
$form = New-Object Windows.Forms.Form
$form.Text = '未言诗集 · 论坛管理账号'
$form.Size = New-Object Drawing.Size(490,310)
$form.StartPosition = 'CenterScreen'
$form.BackColor = [Drawing.Color]::FromArgb(252,248,250)
$form.Font = New-Object Drawing.Font('Microsoft YaHei UI',10)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$caption = New-Object Windows.Forms.Label
$caption.Text = "论坛：https://bbs.hushleaf.top/`r`n管理后台：https://bbs.hushleaf.top/admin.php"
$caption.SetBounds(25,20,435,52)
$form.Controls.Add($caption)
$name = New-Object Windows.Forms.TextBox
$name.Text = $credentials.username
$name.ReadOnly = $true
$name.SetBounds(25,90,420,28)
$form.Controls.Add($name)
$password = New-Object Windows.Forms.TextBox
$password.Text = $credentials.password
$password.ReadOnly = $true
$password.UseSystemPasswordChar = $true
$password.SetBounds(25,132,420,28)
$form.Controls.Add($password)
$show = New-Object Windows.Forms.CheckBox
$show.Text = '显示初始密码'
$show.SetBounds(25,174,180,27)
$show.Add_CheckedChanged({$password.UseSystemPasswordChar = -not $show.Checked})
$form.Controls.Add($show)
$copy = New-Object Windows.Forms.Button
$copy.Text = '复制密码'
$copy.SetBounds(322,170,123,34)
$copy.Add_Click({[Windows.Forms.Clipboard]::SetText($password.Text)})
$form.Controls.Add($copy)
$note = New-Object Windows.Forms.Label
$note.Text = '账号信息使用当前 Windows 账号加密保存。初次登录后可在个人设置中更改密码。'
$note.SetBounds(25,220,420,45)
$form.Controls.Add($note)
[void]$form.ShowDialog()
[Array]::Clear($plain,0,$plain.Length)
