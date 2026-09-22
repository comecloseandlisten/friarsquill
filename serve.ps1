$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add('http://localhost:3101/')
$listener.Start()
Write-Host "Serving on http://localhost:3101"
while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    $path = $ctx.Request.Url.LocalPath
    if ($path -eq '/') { $path = '/index.html' }
    $file = Join-Path 'F:\localvideotranscriber\electron\renderer' $path.TrimStart('/')
    if (Test-Path $file) {
        $bytes = [IO.File]::ReadAllBytes($file)
        $ext = [IO.Path]::GetExtension($file)
        $mime = @{'.html'='text/html;charset=utf-8';'.css'='text/css;charset=utf-8';'.js'='application/javascript;charset=utf-8'}
        $ctx.Response.ContentType = if($mime[$ext]){$mime[$ext]}else{'application/octet-stream'}
        $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    } else {
        $ctx.Response.StatusCode = 404
    }
    $ctx.Response.Close()
}
