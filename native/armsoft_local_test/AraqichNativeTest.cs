using System;
using System.Collections.Generic;
using System.Data;
using System.Data.SqlClient;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Reflection;
using System.Security.Cryptography;
using System.Web.Script.Serialization;
using ArmSoft.Core;
using ArmSoft.Core.ConnectionManagment;
using ArmSoft.Core.DBManagement;
using ArmSoft.Core.Directories;
using ArmSoft.SalesManagement.BusinessObjects;

// Two compile-time flavours, one business-logic source.
// /define:ISOLATED_TEST keeps the disposable local clone flavour. Without it this is the
// production bridge target: no private-field injection, no firewall/LocalDB checks,
// no bootstrap or scenario code is compiled in.
internal static class Program
{
#if ISOLATED_TEST
    internal const string Root = @"G:\Araqich-ERP-Test-20261105-SQL2025";
    internal const string Database = "AraqichTest_20261105";
    internal const string Master = "AraqichSM_20261105";
    internal const string Server = "(localdb)\\AraqichERP20261105";
    internal const string EnvironmentName = "isolated-test";
    internal const string DocumentCommentPrefix = "ARAQICH LOCAL ISOLATED TEST ";
    private const string RunLockRelative = @"evidence\native-run.lock";
#else
    internal const string Root = @"C:\ProgramData\AraqichErpBridge";
    internal const string Database = "SalesManagement";
    internal const string Master = "SMMaster";
    internal const string Server = "192.168.1.4";
    internal const string EnvironmentName = "production";
    internal const string DocumentCommentPrefix = "Araqich routes ";
    private const string RunLockRelative = @"dispatch\native-run.lock";
    private const string ExpectedMachineName = "SQL-SERVER";
    private const string ExpectedProductVersion = "14.0.1000.169";
    private const string ExpectedLibraryVersion = "7.16.3.0";
    private static string ErpPassword;
#endif
    internal const string Vendor = @"C:\Program Files (x86)\Armenian Software\AS-Sales Management 7";
    internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 16777216 };
    internal static Dictionary<string, object> Config;

    [STAThread]
    private static int Main(string[] args)
    {
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        Console.InputEncoding = new System.Text.UTF8Encoding(false);
        AppDomain.CurrentDomain.AssemblyResolve += Resolve;
        try
        {
#if ISOLATED_TEST
            Config = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(Root, @"harness\private-config.json")));
            if (Value("database") != Database || Value("master") != Master || Value("sql_server") != Server
                || Value("sql_login") != "araqich_clone_20261105" || Value("root") != Root)
                throw new Exception("Only the exact isolated local test target is accepted");
            VerifyFirewall();
            VerifyLocal(Connection(Database, true));
            if (args.Length != 1) throw new Exception("Expected bootstrap or probe mode");
            if (File.Exists(Path.Combine(Root,"QUARANTINED.json")) && args[0]!="probe" && args[0]!="dispatch")
                throw new Exception("Local test copy is quarantined; new writes are disabled");
#else
            if (args.Length != 1 || (args[0] != "dispatch" && args[0] != "probe"))
                throw new Exception("Expected dispatch or probe mode");
            LoadProductionConfig();
#endif
            FileStream runLock;
            try
            {
                runLock = new FileStream(Path.Combine(Root,RunLockRelative),
                    FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None);
            }
            catch (IOException ex)
            {
                int code = ex.HResult & 0xffff;
                if (args[0] != "dispatch" || (code != 32 && code != 33)) throw;
                // The native business layer has not been opened and no Store was dispatched.
                Console.WriteLine(Json.Serialize(new { success = false, code = "busy", no_write_dispatched = true }));
                return 0;
            }
            using (runLock)
                return Native(args[0]);
        }
        catch (Exception ex)
        {
            // Never serialize configuration, connection strings or credentials.
            Console.Error.WriteLine(ex.GetType().Name + ": " + ex.Message);
            Console.Error.WriteLine(ex.StackTrace);
            return 1;
        }
    }

    private static Assembly Resolve(object sender, ResolveEventArgs args)
    {
        string name = new AssemblyName(args.Name).Name + ".dll";
        foreach (string directory in new[] { Vendor, Path.Combine(Vendor, "Bin"), Path.Combine(Vendor, "HY-AM") })
        {
            string file = Path.Combine(directory, name);
            if (File.Exists(file)) return Assembly.LoadFrom(file);
        }
        return null;
    }

    internal static string Value(string key) { return Convert.ToString(Config[key]); }

    internal static object Scalar(SqlConnection c, string sql)
    {
        using (SqlCommand q = new SqlCommand(sql, c)) { q.CommandTimeout = 30; return q.ExecuteScalar(); }
    }

#if ISOLATED_TEST
    internal static SqlConnection Connection(string database, bool integrated)
    {
        if (database != Database && database != Master) throw new Exception("Unexpected local database");
        SqlConnectionStringBuilder b = new SqlConnectionStringBuilder();
        b.DataSource = Server;
        b.InitialCatalog = database;
        b.ApplicationName = "Araqich isolated native test";
        b.ConnectTimeout = 10;
        b.Pooling = false;
        b.IntegratedSecurity = integrated;
        if (!integrated) { b.UserID = Value("sql_login"); b.Password = Value("sql_password"); }
        SqlConnection c = new SqlConnection(b.ConnectionString);
        c.Open();
        return c;
    }

    internal static void VerifyLocal(SqlConnection c)
    {
        using (c)
        {
            string machine = Convert.ToString(Scalar(c, "SELECT CONVERT(nvarchar(128),SERVERPROPERTY('MachineName'))"));
            if (!machine.Equals(Environment.MachineName, StringComparison.OrdinalIgnoreCase)
                || !machine.Equals(Value("expected_machine"), StringComparison.OrdinalIgnoreCase))
                throw new Exception("SQL connection is not this computer");
            if (c.Database != Database && c.Database != Master) throw new Exception("Wrong current database");
            if (Convert.ToInt32(Scalar(c,"SELECT CONVERT(int,SERVERPROPERTY('IsLocalDB'))"))!=1)
                throw new Exception("Only the independent owner-local SQL2025 instance is accepted");
        }
    }

    private static void VerifyFirewall()
    {
        Type t = Type.GetTypeFromProgID("HNetCfg.FwPolicy2");
        dynamic policy = Activator.CreateInstance(t);
        dynamic rule = policy.Rules.Item("Araqich local ERP test SQL2025 - block external connections");
        string exe = Assembly.GetExecutingAssembly().Location;
        if (!rule.Enabled || (int)rule.Action != 0 || (int)rule.Direction != 2
            || !String.Equals((string)rule.ApplicationName, exe, StringComparison.OrdinalIgnoreCase))
            throw new Exception("Required per-program outbound block is not enabled");
        // The process must be unable to connect to the real ERP SQL endpoint.
        using (TcpClient client = new TcpClient())
        {
            IAsyncResult attempt = client.BeginConnect("192.168.1.4", 1433, null, null);
            if (attempt.AsyncWaitHandle.WaitOne(1500))
            {
                try { client.EndConnect(attempt); }
                catch (SocketException) { return; }
                throw new Exception("External ERP endpoint reachable; native tests forbidden");
            }
        }
    }
#else
    // Everything the production flavour needs is read and validated here, before any
    // business-library call or network connection is made.
    private static void LoadProductionConfig()
    {
        Config = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(Root, "private-config.json")));
        if (Config == null || Config.Count != 2 || !(Config.ContainsKey("erp_user") && Config["erp_user"] is string)
            || !(Config.ContainsKey("erp_password_protected") && Config["erp_password_protected"] is string)
            || String.IsNullOrWhiteSpace(Value("erp_user")) || String.IsNullOrWhiteSpace(Value("erp_password_protected")))
            throw new Exception("Production bridge configuration is invalid");
        byte[] plain = ProtectedData.Unprotect(Convert.FromBase64String(Value("erp_password_protected")), null, DataProtectionScope.LocalMachine);
        ErpPassword = new System.Text.UTF8Encoding(false).GetString(plain);
        Array.Clear(plain, 0, plain.Length);
        if (ErpPassword.Length == 0) throw new Exception("Production bridge configuration is invalid");
    }
#endif

    private static int Native(string mode)
    {
        SettingsHelper.InitCore(SystemTypes.SalesManagement, Localizations.Armenia, Master,
            ArmSoft.SalesManagement.BusinessObjects.Resources.ResourceManager,
            typeof(BusinessObjectsServer).Assembly.GetName().Version);
        SettingsHelper.ServerName = Server; // process-local setter, no registry write
        SettingsHelper.SetCurrentThreadCulture("hy-AM");
#if ISOLATED_TEST
        if (mode == "bootstrap")
        {
            using (SqlConnection c = Connection(Master, true))
            {
                if (Convert.ToInt32(Scalar(c, "SELECT COUNT(*) FROM sys.tables WHERE name='USERS'")) == 0)
                    new MasterDBCreater(c).Create();
                using (SqlCommand q = new SqlCommand("SELECT COUNT(*) FROM USERS WHERE fNAME=@name", c))
                {
                    q.Parameters.AddWithValue("@name", Value("erp_user"));
                    if (Convert.ToInt32(q.ExecuteScalar()) == 0)
                        UsersDataProviderBase.Add(Value("erp_user"), "Araqich isolated test administrator",
                            Value("erp_password"), true, true, c);
                }
                using (SqlCommand q = new SqlCommand("IF NOT EXISTS (SELECT 1 FROM DATABASES WHERE fDBNAME=@db) "
                    + "INSERT INTO DATABASES(fDBNAME,fCAPTION,fSERVERNAME) VALUES(@db,@caption,@server)", c))
                {
                    q.Parameters.AddWithValue("@db", Database);
                    q.Parameters.AddWithValue("@caption", "Araqich isolated local ERP test");
                    q.Parameters.AddWithValue("@server", Server);
                    q.ExecuteNonQuery();
                }
            }
            Console.WriteLine(Json.Serialize(new { phase = "native_master_bootstrapped", database = Database, master = Master }));
            return 0;
        }
#endif
        if (mode == "dispatch") return NativeCases.Dispatch();
#if ISOLATED_TEST
        if (mode != "probe") return NativeCases.Run(mode);
#endif
        BusinessObjectsServer bos = OpenNative();
        Console.WriteLine(Json.Serialize(new { phase = "native_login_verified", database = bos.DatabaseName,
            user = bos.UserName, user_id = bos.UserId, native_version = typeof(BusinessObjectsServer).Assembly.GetName().Version.ToString() }));
        bos.Logout();
        return 0;
    }

    internal static BusinessObjectsServer OpenNative()
    {
        BusinessObjectsServer bos = new BusinessObjectsServer();
#if ISOLATED_TEST
        object manager = bos.ConnectionManager();
        // TEST HARNESS ONLY: inject the dedicated clone SQL credential, never
        // vendor launcher credentials or production authentication. This does
        // not skip Configure's ERP password, role or subsystem checks. Private
        // connection injection is not a production integration contract.
        Type type = typeof(ConnectionsManager);
        type.GetField("sqlServerLogin", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(manager, Value("sql_login"));
        type.GetField("sqlServerPassword", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(manager, Value("sql_password"));
        bos.Configure(new LocalDBLoginResult(Server, Database, Value("erp_user"), Value("erp_password")), true);
        SqlConnection c = bos.Connection();
        string machine = Convert.ToString(Scalar(c, "SELECT CONVERT(nvarchar(128),SERVERPROPERTY('MachineName'))"));
        if (c.Database != Database || !machine.Equals(Environment.MachineName, StringComparison.OrdinalIgnoreCase))
            throw new Exception("Native connection escaped the local clone");
        if (Convert.ToInt32(Scalar(c,"SELECT CONVERT(int,SERVERPROPERTY('IsLocalDB'))"))!=1)
            throw new Exception("Native connection is not the independent LocalDB instance");
        if (Convert.ToInt32(Scalar(c, "SELECT IS_SRVROLEMEMBER('sysadmin')")) != 0)
            throw new Exception("Native test SQL login must not be sysadmin");
        return bos;
#else
        // No private fields: with an empty sqlServerLogin the library resolves its own SQL
        // credentials exactly as the ArmSoft desktop app does. Authorisation comes from a
        // dedicated non-administrator ERP user. Any mismatch below stops with no write.
        if (typeof(BusinessObjectsServer).Assembly.GetName().Version.ToString() != ExpectedLibraryVersion)
            throw new Exception("Unexpected ArmSoft business library version");
        LocalDBLoginResult login = new LocalDBLoginResult(Server, Database, Value("erp_user"), ErpPassword);
        bos.Configure(login, true);
        if (login.IsAdmin) throw new Exception("Bridge refuses an ERP administrator account");
        SqlConnection c = bos.Connection();
        if (Convert.ToString(Scalar(c, "SELECT DB_NAME()")) != Database)
            throw new Exception("Native connection is not the expected ERP database");
        if (Convert.ToString(Scalar(c, "SELECT CONVERT(nvarchar(128),SERVERPROPERTY('MachineName'))")) != ExpectedMachineName)
            throw new Exception("Native connection is not the expected ERP server");
        if (Convert.ToString(Scalar(c, "SELECT CONVERT(nvarchar(128),SERVERPROPERTY('ProductVersion'))")) != ExpectedProductVersion)
            throw new Exception("Unexpected SQL Server version");
        return bos;
#endif
    }
}
