// Authenticated HTTP bridge from the route-optimizer site to the ArmSoft native process.
// .NET Framework, BCL only, no ArmSoft references. Protocol reference: docs/erp-chain-bridge.md.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.ServiceProcess;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

internal static class BridgeProgram
{
    private const string Usage = "Usage: AraqichErpBridge.exe (no arguments, run by the service controller) | --console --config <path> | protect";

    private static int Main(string[] args)
    {
        try
        {
            if (args.Length == 1 && args[0] == "protect") return Protect();
            if (args.Length == 3 && args[0] == "--console" && args[1] == "--config") return RunConsole(args[2]);
            if (args.Length == 0) { ServiceBase.Run(new BridgeService()); return 0; }
            Console.Error.WriteLine(Usage);
            return 2;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex.GetType().Name + ": " + ex.Message);
            return 1;
        }
    }

    // Reads a secret from stdin and prints its DPAPI LocalMachine protection in base64.
    private static int Protect()
    {
        string secret;
        using (var reader = new StreamReader(Console.OpenStandardInput(), new UTF8Encoding(false), true))
            secret = reader.ReadToEnd().TrimEnd('\r', '\n');
        if (secret.Length == 0)
        {
            Console.Error.WriteLine("Empty secret on stdin");
            return 1;
        }
        byte[] plain = new UTF8Encoding(false).GetBytes(secret);
        byte[] cipher = ProtectedData.Protect(plain, null, DataProtectionScope.LocalMachine);
        Array.Clear(plain, 0, plain.Length);
        Console.WriteLine(Convert.ToBase64String(cipher));
        return 0;
    }

    // For tests: loopback prefix only, stops gracefully when stdin closes.
    private static int RunConsole(string configPath)
    {
        BridgeConfig config = BridgeConfig.Load(configPath, true);
        var server = new BridgeServer(config, Path.GetDirectoryName(Path.GetFullPath(configPath)));
        server.Start();
        Console.WriteLine("listening");
        Console.In.ReadToEnd();
        server.BeginStop();
        while (!server.WaitForWrites(TimeSpan.FromSeconds(5))) { }
        server.CloseListener();
        return 0;
    }
}

internal sealed class BridgeConfig
{
    internal string ListenPrefix;
    internal HashSet<string> AllowedIps;
    internal byte[] Secret;
    internal string NativeExe;

    internal static BridgeConfig Load(string path, bool requireLoopback)
    {
        var raw = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8));
        var config = new BridgeConfig();
        config.ListenPrefix = RequireString(raw, "listen_prefix");
        if (requireLoopback && !IsLoopbackPrefix(config.ListenPrefix))
            throw new InvalidOperationException("Console mode accepts only a loopback listen_prefix");
        object ips;
        if (!raw.TryGetValue("allowed_ips", out ips) || !(ips is System.Collections.IEnumerable) || ips is string)
            throw new InvalidOperationException("allowed_ips must be a list");
        config.AllowedIps = new HashSet<string>(StringComparer.Ordinal);
        foreach (object ip in (System.Collections.IEnumerable)ips)
            config.AllowedIps.Add(BridgeServer.NormalizeIp(IPAddress.Parse(Convert.ToString(ip, CultureInfo.InvariantCulture))));
        if (config.AllowedIps.Count == 0) throw new InvalidOperationException("allowed_ips must not be empty");
        config.Secret = ProtectedData.Unprotect(Convert.FromBase64String(RequireString(raw, "secret_protected")), null, DataProtectionScope.LocalMachine);
        if (config.Secret.Length == 0) throw new InvalidOperationException("secret_protected is empty");
        config.NativeExe = RequireString(raw, "native_exe");
        if (!Path.IsPathRooted(config.NativeExe) || !File.Exists(config.NativeExe))
            throw new InvalidOperationException("native_exe must be an existing absolute path");
        return config;
    }

    private static string RequireString(Dictionary<string, object> raw, string key)
    {
        object value;
        if (!raw.TryGetValue(key, out value) || !(value is string) || String.IsNullOrWhiteSpace((string)value))
            throw new InvalidOperationException("Missing or invalid " + key);
        return (string)value;
    }

    private static bool IsLoopbackPrefix(string prefix)
    {
        if (!prefix.StartsWith("http://", StringComparison.OrdinalIgnoreCase)) return false;
        string authority = prefix.Substring(7);
        int slash = authority.IndexOf('/');
        if (slash >= 0) authority = authority.Substring(0, slash);
        string host = authority;
        if (authority.StartsWith("[", StringComparison.Ordinal))
            host = authority.Substring(0, authority.IndexOf(']') + 1);
        else if (authority.Contains(":"))
            host = authority.Substring(0, authority.IndexOf(':'));
        return host == "127.0.0.1" || host == "[::1]" || host.Equals("localhost", StringComparison.OrdinalIgnoreCase);
    }
}

internal sealed class BridgeServer
{
    private sealed class Reply
    {
        internal int Status;
        internal string Outcome;
        internal byte[] Body;
    }

    private sealed class RequestState
    {
        internal string Action = "-";
        internal string Id = "-";
        internal bool Authenticated; // set only after the nonce has been accepted
        internal bool HoldsWrite;    // this request owns one count in runningWrites
    }

    private const string TimestampHeader = "X-Araqich-Timestamp";
    private const string NonceHeader = "X-Araqich-Nonce";
    private const string SignatureHeader = "X-Araqich-Signature";
    private const int MaxBodyBytes = 2 * 1024 * 1024;
    private const int DiscardBodyLimit = 8 * 1024 * 1024; // answer oversized requests without buffering them
    private const int WindowSeconds = 120;
    private const int ReadKillMs = 120 * 1000;
    private const int WriteSoftTimeoutMs = 900 * 1000;

    private static readonly HashSet<string> Actions = new HashSet<string>(StringComparer.Ordinal)
        { "options", "preview", "execute", "status", "reconcile-preview", "reconcile-execute" };
    private static readonly HashSet<string> WriteActions = new HashSet<string>(StringComparer.Ordinal)
        { "execute", "reconcile-execute" };

    private readonly BridgeConfig config;
    private readonly string logDirectory;
    private readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
    private readonly HttpListener listener = new HttpListener();
    private readonly object nonceLock = new object();
    private readonly Dictionary<string, long> seenNonces = new Dictionary<string, long>(StringComparer.Ordinal);
    private readonly object writesLock = new object();
    private readonly object logLock = new object();
    private readonly long startedUnix = UnixNow();
    private int runningWrites;
    private volatile bool stopping;

    internal BridgeServer(BridgeConfig config, string root)
    {
        this.config = config;
        logDirectory = Path.Combine(root, "logs");
    }

    internal void Start()
    {
        Directory.CreateDirectory(logDirectory);
        listener.Prefixes.Add(config.ListenPrefix);
        listener.Start();
        new Thread(AcceptLoop) { IsBackground = true, Name = "bridge-accept" }.Start();
    }

    internal void BeginStop()
    {
        lock (writesLock) stopping = true;
    }

    // Write children are never killed; this returns true only when none remain.
    internal bool WaitForWrites(TimeSpan timeout)
    {
        lock (writesLock)
        {
            if (runningWrites != 0) Monitor.Wait(writesLock, timeout);
            return runningWrites == 0;
        }
    }

    internal void CloseListener()
    {
        try { listener.Close(); }
        catch (Exception) { }
    }

    private void AcceptLoop()
    {
        while (true)
        {
            HttpListenerContext context;
            try { context = listener.GetContext(); }
            catch (Exception)
            {
                if (stopping) return;
                Log("-", "-", "accept_failed", 0, 0);
                Thread.Sleep(1000);
                continue;
            }
            // A dedicated thread: a write request holds it up to the soft timeout.
            new Thread(Handle) { IsBackground = true, Name = "bridge-request" }.Start(context);
        }
    }

    private void Handle(object value)
    {
        var context = (HttpListenerContext)value;
        var watch = Stopwatch.StartNew();
        var state = new RequestState();
        string nonce = NormalizeNonce(context.Request.Headers[NonceHeader]);
        Reply reply;
        try
        {
            reply = Route(context, nonce, state);
        }
        catch (Exception)
        {
            // Reached possibly after a spawn, so this reply must never claim no write.
            reply = Unknown(500, "internal_error");
        }
        try
        {
            try
            {
                // A reply bound to the caller's nonce is proof the bridge authenticated that request.
                // Anything else is signed over an empty nonce, so the client cannot accept it as an answer.
                Send(context, state.Authenticated ? nonce : "", reply.Status, reply.Body);
            }
            catch (Exception) { reply.Outcome += "/send_failed"; }
            finally
            {
                try { context.Response.Close(); }
                catch (Exception) { }
            }
            Log(state.Action, state.Id, reply.Outcome, reply.Status, watch.ElapsedMilliseconds);
        }
        finally
        {
            // Last, so a stopping service keeps running until this write is answered and logged.
            if (state.HoldsWrite) ReleaseWrite();
        }
    }

    private Reply Route(HttpListenerContext context, string nonce, RequestState state)
    {
        HttpListenerRequest request = context.Request;
        if (!config.AllowedIps.Contains(NormalizeIp(request.RemoteEndPoint.Address))) return Refuse(403, "forbidden");
        bool tooLarge;
        byte[] body = ReadBody(request, out tooLarge);
        if (tooLarge) return Refuse(413, "body_too_large");
        string timestamp = request.Headers[TimestampHeader];
        if (!IsFresh(timestamp)) return Refuse(401, "stale_timestamp");
        if (nonce.Length == 0) return Refuse(401, "bad_signature");
        string path = request.Url.AbsolutePath;
        string canonical = "v1\n" + request.HttpMethod + "\n" + path + "\n" + timestamp + "\n" + nonce + "\n" + Hex(Sha256(body));
        if (!ConstantTimeEquals(Hex(Hmac(canonical)), request.Headers[SignatureHeader] ?? "")) return Refuse(401, "bad_signature");
        // A replay may be a copy of a request that is really running, so it can never claim no write.
        if (!RememberNonce(nonce)) return Unknown(401, "replayed_nonce");
        state.Authenticated = true;
        if (stopping) return Refuse(503, "stopping");
        if (path == "/v1/health" && request.HttpMethod == "GET") return Health();
        // Not "not_found": the site reads that code as native reporting that no journal exists.
        if (path != "/v1/dispatch" || request.HttpMethod != "POST") return Refuse(404, "unknown_endpoint");

        Dictionary<string, object> map;
        try { map = json.Deserialize<Dictionary<string, object>>(new UTF8Encoding(false).GetString(body)); }
        catch (Exception) { map = null; }
        object value;
        if (map == null || !map.TryGetValue("action", out value) || !(value is string) || !Actions.Contains((string)value))
            return Refuse(400, "unknown_action");
        state.Action = (string)value;
        object raw;
        Guid parsed;
        if (map.TryGetValue("id", out raw) && raw is string && Guid.TryParse((string)raw, out parsed))
            state.Id = parsed.ToString("D");
        return RunNative(body, WriteActions.Contains(state.Action), state);
    }

    // One short-lived native process per request. Write children are never killed.
    private Reply RunNative(byte[] body, bool write, RequestState state)
    {
        if (write)
        {
            lock (writesLock)
            {
                if (stopping) return Refuse(503, "stopping");
                runningWrites++;
                state.HoldsWrite = true; // released in Handle after the reply is sent
            }
        }
        Process process = null;
        try
        {
            var start = new ProcessStartInfo(config.NativeExe, "dispatch")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                WorkingDirectory = Path.GetDirectoryName(config.NativeExe)
            };
            try { process = Process.Start(start); }
            catch (Exception) { return Refuse(502, "spawn_failed"); }
            int pid = process.Id;
            var stdout = new MemoryStream();
            var stderr = new MemoryStream();
            Task readOut = process.StandardOutput.BaseStream.CopyToAsync(stdout);
            Task readErr = process.StandardError.BaseStream.CopyToAsync(stderr);
            var stdin = process.StandardInput.BaseStream;
            Task.Run(() =>
            {
                try { stdin.Write(body, 0, body.Length); stdin.Close(); }
                catch (IOException) { }
                catch (ObjectDisposedException) { }
            });
            if (!process.WaitForExit(write ? WriteSoftTimeoutMs : ReadKillMs))
            {
                if (!write)
                {
                    try { process.Kill(); process.WaitForExit(5000); }
                    catch (Exception) { }
                    SaveStderr(readErr, stderr, pid);
                    return Unknown(502, "read_killed_after_timeout");
                }
                state.HoldsWrite = false; // the watcher owns the release from here
                Process running = process;
                process = null; // the watcher disposes it
                Task.Run(() =>
                {
                    try
                    {
                        running.WaitForExit();
                        SaveStderr(readErr, stderr, pid);
                        running.Dispose();
                    }
                    finally { ReleaseWrite(); }
                });
                return Unknown(502, "write_soft_timeout");
            }
            Task.WaitAll(new[] { readOut, readErr }, 15000);
            SaveStderr(readErr, stderr, pid);
            if (process.ExitCode != 0 || stdout.Length == 0)
                return Unknown(502, "native_exit_" + process.ExitCode.ToString(CultureInfo.InvariantCulture));
            return new Reply { Status = 200, Outcome = OutcomeOf(stdout.ToArray()), Body = stdout.ToArray() };
        }
        finally
        {
            if (process != null) process.Dispose();
        }
    }

    // Native writes only the exception type, message and stack to stderr, never configuration values.
    private void SaveStderr(Task reader, MemoryStream stderr, int pid)
    {
        try
        {
            reader.Wait(5000);
            if (stderr.Length == 0) return;
            string name = "native-" + DateTime.UtcNow.ToString("yyyyMMddTHHmmssfff", CultureInfo.InvariantCulture)
                + "-" + pid.ToString(CultureInfo.InvariantCulture) + ".stderr.txt";
            File.WriteAllBytes(Path.Combine(logDirectory, name), stderr.ToArray());
        }
        catch (Exception) { } // A logging failure must not change a request outcome.
    }

    private void ReleaseWrite()
    {
        lock (writesLock)
        {
            runningWrites--;
            Monitor.PulseAll(writesLock);
        }
    }

    private Reply Health()
    {
        return new Reply
        {
            Status = 200,
            Outcome = "health",
            Body = Encoding.UTF8.GetBytes("{\"success\":true,\"status\":\"ok\",\"native_exe_present\":"
                + (File.Exists(config.NativeExe) ? "true" : "false") + "}")
        };
    }

    // Rejections before spawn: signed, and no native process was started for them.
    private static Reply Refuse(int status, string code)
    {
        return new Reply
        {
            Status = status,
            Outcome = "rejected:" + code,
            Body = Encoding.UTF8.GetBytes("{\"success\":false,\"code\":\"" + code + "\",\"no_write_dispatched\":true}")
        };
    }

    // A possible write happened or cannot be excluded: the client must treat it as unknown.
    private static Reply Unknown(int status, string outcome)
    {
        return new Reply
        {
            Status = status,
            Outcome = "unknown:" + outcome,
            Body = Encoding.UTF8.GetBytes("{\"success\":false,\"code\":\"unknown\"}")
        };
    }

    private string OutcomeOf(byte[] stdout)
    {
        try
        {
            var map = json.Deserialize<Dictionary<string, object>>(new UTF8Encoding(false).GetString(stdout));
            object success, code;
            if (map.TryGetValue("success", out success) && success is bool && (bool)success) return "ok";
            return "refused:" + (map.TryGetValue("code", out code) ? Convert.ToString(code, CultureInfo.InvariantCulture) : "unspecified");
        }
        catch (Exception) { return "unparsed_stdout"; }
    }

    private void Send(HttpListenerContext context, string nonce, int status, byte[] body)
    {
        string canonical = "v1-resp\n" + nonce + "\n" + status.ToString(CultureInfo.InvariantCulture) + "\n" + Hex(Sha256(body));
        HttpListenerResponse response = context.Response;
        response.StatusCode = status;
        response.ContentType = "application/json; charset=utf-8";
        response.ContentLength64 = body.Length;
        response.Headers[SignatureHeader] = Hex(Hmac(canonical));
        response.OutputStream.Write(body, 0, body.Length);
        response.OutputStream.Close();
    }

    private static byte[] ReadBody(HttpListenerRequest request, out bool tooLarge)
    {
        tooLarge = request.ContentLength64 > MaxBodyBytes;
        var kept = new MemoryStream();
        byte[] chunk = new byte[64 * 1024];
        long total = 0;
        int read;
        while ((read = request.InputStream.Read(chunk, 0, chunk.Length)) > 0)
        {
            total += read;
            if (total > DiscardBodyLimit) { tooLarge = true; break; }
            if (total > MaxBodyBytes) tooLarge = true;
            if (!tooLarge) kept.Write(chunk, 0, read);
        }
        return kept.ToArray();
    }

    private bool IsFresh(string timestamp)
    {
        long value;
        if (timestamp == null || !long.TryParse(timestamp, NumberStyles.None, CultureInfo.InvariantCulture, out value)) return false;
        // The nonce cache is in memory: a request older than this process could be a replay it cannot recognise.
        return value >= startedUnix && Math.Abs(UnixNow() - value) <= WindowSeconds;
    }

    private bool RememberNonce(string nonce)
    {
        long now = UnixNow();
        lock (nonceLock)
        {
            var expired = new List<string>();
            foreach (KeyValuePair<string, long> entry in seenNonces)
                if (entry.Value < now) expired.Add(entry.Key);
            foreach (string key in expired) seenNonces.Remove(key);
            if (seenNonces.ContainsKey(nonce)) return false;
            // A request stays valid at most 2 x window after acceptance; a margin covers clock jitter.
            seenNonces[nonce] = now + 2 * WindowSeconds + 10;
            return true;
        }
    }

    private static string NormalizeNonce(string nonce)
    {
        if (String.IsNullOrEmpty(nonce) || nonce.Length > 128) return "";
        foreach (char c in nonce)
            if (!(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'z') && !(c >= 'A' && c <= 'Z') && c != '-' && c != '_') return "";
        return nonce;
    }

    internal static string NormalizeIp(IPAddress address)
    {
        if (address.IsIPv4MappedToIPv6) address = address.MapToIPv4();
        return address.ToString();
    }

    private static long UnixNow()
    {
        return (long)(DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
    }

    private static bool ConstantTimeEquals(string expected, string actual)
    {
        byte[] a = Encoding.UTF8.GetBytes(expected), b = Encoding.UTF8.GetBytes(actual);
        if (a.Length != b.Length) return false;
        int difference = 0;
        for (int i = 0; i < a.Length; i++) difference |= a[i] ^ b[i];
        return difference == 0;
    }

    private byte[] Hmac(string canonical)
    {
        using (var hmac = new HMACSHA256(config.Secret)) return hmac.ComputeHash(new UTF8Encoding(false).GetBytes(canonical));
    }

    private static byte[] Sha256(byte[] data)
    {
        using (SHA256 hash = SHA256.Create()) return hash.ComputeHash(data);
    }

    private static string Hex(byte[] data)
    {
        return BitConverter.ToString(data).Replace("-", "").ToLowerInvariant();
    }

    private void Log(string action, string id, string outcome, int status, long milliseconds)
    {
        string line = DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture) + " action=" + action + " id=" + id
            + " outcome=" + outcome + " status=" + status.ToString(CultureInfo.InvariantCulture)
            + " ms=" + milliseconds.ToString(CultureInfo.InvariantCulture) + Environment.NewLine;
        try
        {
            lock (logLock)
                File.AppendAllText(Path.Combine(logDirectory, "bridge-" + DateTime.UtcNow.ToString("yyyyMMdd", CultureInfo.InvariantCulture) + ".log"), line);
        }
        catch (Exception) { } // A logging failure must not change a request outcome.
    }
}

internal sealed class BridgeService : ServiceBase
{
    private const string Root = @"C:\ProgramData\AraqichErpBridge";
    private BridgeServer server;

    internal BridgeService()
    {
        ServiceName = "AraqichErpBridge";
        CanStop = true;
        CanShutdown = true;
    }

    protected override void OnStart(string[] args)
    {
        server = new BridgeServer(BridgeConfig.Load(Path.Combine(Root, "bridge-config.json"), false), Root);
        server.Start();
    }

    protected override void OnStop() { StopAndWait(); }

    protected override void OnShutdown() { StopAndWait(); }

    private void StopAndWait()
    {
        server.BeginStop();
        // Running write children are never killed: keep asking the service controller for time.
        while (!server.WaitForWrites(TimeSpan.FromSeconds(10)))
        {
            try { RequestAdditionalTime(25000); }
            catch (InvalidOperationException) { } // not in a pending state during OS shutdown; the OS limit applies
        }
        server.CloseListener();
    }
}
