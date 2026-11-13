using System;
using System.Collections.Generic;
using System.Data.SqlClient;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using ArmSoft.Core;
using ArmSoft.SalesManagement.BusinessObjects;
using ArmSoft.SalesManagement.BusinessObjects.Constants;
using ArmSoft.SalesManagement.BusinessObjects.DataProviders.DropDowns;
using ArmSoft.SalesManagement.BusinessObjects.Directories;
using ArmSoft.SalesManagement.BusinessObjects.Documents;
using ArmSoft.SalesManagement.BusinessObjects.Accountings;

internal static partial class NativeCases
{
    private static DateTime Day = new DateTime(2026, 11, 5);
    private static string Warehouse = "000";

#if ISOLATED_TEST
    internal static int Run(string mode)
    {
        if (mode!="recovery-plan" && !new[] { "plan", "full", "partial", "lost-response", "invalid-warehouse", "multi", "gift", "deposit", "exchange", "containers", "seed-complex", "seed-history", "batch", "batch-available", "concurrent", "sql2025-smoke", "sql2025-smoke-ok" }.Contains(mode))
            throw new Exception("Unknown native scenario");
        BusinessObjectsServer bos = Program.OpenNative();
        try
        {
            if(mode=="recovery-plan")
            {Console.WriteLine(Program.Json.Serialize(Prepare(bos,"full",DispatchClaims(null),"000")));return 0;}
            if(mode=="seed-complex")return SeedComplex(bos);
            if(mode=="seed-history")return SeedHistory(bos);
            if(mode=="batch"||mode=="batch-available")return Batch(bos,mode);
            string scenario = mode == "plan" ? "full" : mode;
            string path = Path.Combine(Program.Root, "evidence", "case-" + scenario + ".json");
            Dictionary<string, object> state;
            if (File.Exists(path))
            {
                state = Program.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path));
                Warehouse=Convert.ToString(state["warehouse"]);
                if (Convert.ToString(state["status"]) != "planned") return Recheck(bos, state, path);
            }
            else
            {
                state = Prepare(bos, scenario);
                Save(path, state);
            }
            if (mode == "plan") { Console.WriteLine(Program.Json.Serialize(state)); return 0; }
            return Execute(bos, state, path);
        }
        finally { bos.Logout(); }
    }

    private static int SeedHistory(BusinessObjectsServer bos)
    {
        string path=Path.Combine(Program.Root,"evidence","history-case-fixtures.json");
        if(File.Exists(path))throw new Exception("Historical fixture already has a journal; no automatic repeat");
        Warehouse="000";
        var state=new Dictionary<string,object>{{"status","preparing"},{"database",Program.Database},
            {"purpose","New disposable local orders cloned from historical examples, not production changes"}};
        var orders=new List<Dictionary<string,object>>();state["orders"]=orders;Save(path,state);
        var needs=new Dictionary<string,decimal>();
        foreach(string scenario in new[]{"exchange","containers"})
        {
            string condition=scenario=="exchange"?"EXISTS(SELECT 1 FROM SALEDOCEXCHANGES s WHERE s.fISN=o.fISN)":
                "EXISTS(SELECT 1 FROM SALEDOCDETAILS s JOIN PRODUCTS p ON p.fID=s.fPRODUCTID WHERE s.fISN=o.fISN AND p.fCONTAINER=1)";
            List<Guid> ids=new List<Guid>();
            using(SqlCommand q=new SqlCommand("SELECT TOP(30) o.fISN FROM ORDERS o WHERE o.fSTATE=2 AND o.fDATE<=@day AND "+condition+" ORDER BY o.fDATE DESC,o.fDOCNUM DESC",bos.Connection()))
            {q.Parameters.AddWithValue("@day",Day);using(SqlDataReader r=q.ExecuteReader())while(r.Read())ids.Add(r.GetGuid(0));}
            if(ids.Count==0)throw new Exception("No historical native example for "+scenario);
            SalesOrder source=SalesOrder.LoadInstance(bos,ids[0]);
            SalesOrder order=(SalesOrder)source.Clone(false);
            if(order.StoredInDb || order.ISN==source.ISN)throw new Exception("Native clone is not a new independent document");
            order.DocNum="";order.DocDate=Day;order.DeliveryDate=Day;order.Parents.Clear();
            order.Comment="ARAQICH LOCAL TEST FIXTURE "+scenario;order.B2BId=null;
            order.ExportedToTaxProgram=false;order.TaxSeriaNumber="";order.SubmitDate=null;order.CoSignDate=null;
            var record=new Dictionary<string,object>{{"scenario",scenario},{"historical_order_isn",source.ISN.ToString()},
                {"new_order_isn",order.ISN.ToString()},{"status","order-pending"}};
            orders.Add(record);Save(path,state);
            order.Store(new StoreRequest{MakeDraft=false});
            record["new_order_num"]=order.DocNum;record["status"]="order-created";Save(path,state);
            ProductsProviding p=MakeProvision(bos,order,"historical-stock-fixture",order.VanAgentCode,order.DeliveryCar);
            foreach(var row in p.ProductsList){if(!needs.ContainsKey(row.Code))needs[row.Code]=0;needs[row.Code]+=row.Quantity;}
        }
        var input=new ArmSoft.SalesManagement.BusinessObjects.Exchange.ProductsInput{DocDate=Day,Storage=Warehouse,
            Comment="ARAQICH LOCAL TEST FIXTURE ONLY - historical exchange/container stock",Accepter="ARAQICH_TEST",IsDraft=false};
        input.SetOtherSystemSendStatus("1");
        var stockRows=new List<Dictionary<string,object>>();
        foreach(var pair in needs)
        {
            var product=bos.Cache.Products.GetProductByCode(pair.Key);decimal before=Stock(bos,product.Id,null),add=Math.Max(0,pair.Value+10-before);
            if(add==0)continue;if(add>100000)throw new Exception("Historical fixture stock size out of range");
            var r=input.AddProductsRow();r.Product=pair.Key;r.Quantity=add;r.Price=1;r.Sum=add;
            stockRows.Add(new Dictionary<string,object>{{"code",pair.Key},{"product_id",product.Id},{"before",before},{"input_quantity",add}});
        }
        state["stock_rows"]=stockRows;state["status"]="input-pending";Save(path,state);
        if(stockRows.Count>0)
        {
            input.Import(bos,ArmSoft.Core.Exchange.ImportMode.PreserveState,false,false);
            foreach(var row in stockRows)
            {
                decimal after=Stock(bos,Convert.ToInt32(row["product_id"]),null);row["after"]=after;
                if(after-Convert.ToDecimal(row["before"])!=Convert.ToDecimal(row["input_quantity"]))throw new Exception("Historical stock fixture readback mismatch");
            }
            state["input_isn"]=input.DocIsn.ToString();
        }
        state["status"]="complete";Save(path,state);Console.WriteLine(Program.Json.Serialize(state));return 0;
    }

    private static int SeedComplex(BusinessObjectsServer bos)
    {
        string path=Path.Combine(Program.Root,"evidence","complex-stock-fixture.json");
        if(File.Exists(path))throw new Exception("Stock fixture already has a journal; never repeat automatically");
        Warehouse="000";
        var needs=new Dictionary<string,decimal>();var chosen=new List<string>();
        bool multi=false,gift=false,deposit=false;
        foreach(Guid id in CandidateIds(bos))
        {
            SalesOrder order=SalesOrder.LoadInstance(bos,id);
            if(order.IsDraft||order.HasProviding||order.HasSale||order.Sum<=0||order.ProductsList.Count==0||order.ExportedToTaxProgram)continue;
            bool use=(!multi&&order.ProductsList.Count>1)||(!gift&&order.GiftsList.Count>0)||(!deposit&&order.OnDepositsList.Count>0);
            if(!use)continue;
            // Only seed disposable test-copy inventory through a real native
            // goods-input document. This never changes the source backup or ERP.
            ProductsProviding p=MakeProvision(bos,order,"stock-fixture",order.VanAgentCode,order.DeliveryCar);
            foreach(var row in p.ProductsList)
            {if(!needs.ContainsKey(row.Code))needs[row.Code]=0;needs[row.Code]+=row.Quantity;}
            chosen.Add(order.DocNum);multi|=order.ProductsList.Count>1;gift|=order.GiftsList.Count>0;deposit|=order.OnDepositsList.Count>0;
            if(multi&&gift&&deposit)break;
        }
        if(!multi||!gift||!deposit)throw new Exception("No source examples for all requested complex fixtures");
        var input=new ArmSoft.SalesManagement.BusinessObjects.Exchange.ProductsInput {
            DocDate=Day,Storage=Warehouse,Comment="ARAQICH LOCAL TEST FIXTURE ONLY - complex scenario stock",
            Accepter="ARAQICH_TEST",IsDraft=false};
        input.SetOtherSystemSendStatus("1");
        var stockRows=new List<Dictionary<string,object>>();
        foreach(var pair in needs)
        {
            var product=bos.Cache.Products.GetProductByCode(pair.Key);
            decimal before=Stock(bos,product.Id,null),add=Math.Max(0,pair.Value+10-before);
            if(add<=0)continue;
            if(add>100000)throw new Exception("Unexpected stock shortage size; fixture not created");
            var row=input.AddProductsRow();row.Product=pair.Key;row.Quantity=add;row.Price=1;row.Sum=add;
            stockRows.Add(new Dictionary<string,object>{{"code",pair.Key},{"product_id",product.Id},{"before",before},{"input_quantity",add}});
        }
        var state=new Dictionary<string,object>{{"status","input-pending"},{"database",Program.Database},
            {"warehouse",Warehouse},{"source_examples",chosen},{"rows",stockRows},
            {"purpose","Disposable local test inventory; native inputs, not production fixes"}};
        Save(path,state);
        input.Import(bos,ArmSoft.Core.Exchange.ImportMode.PreserveState,false,false);
        foreach(var row in stockRows)
        {
            decimal after=Stock(bos,Convert.ToInt32(row["product_id"]),null);row["after"]=after;
            if(after-Convert.ToDecimal(row["before"])!=Convert.ToDecimal(row["input_quantity"]))throw new Exception("Fixture input stock readback mismatch");
        }
        state["input_isn"]=input.DocIsn.ToString().ToUpperInvariant();state["status"]="complete";Save(path,state);
        Console.WriteLine(Program.Json.Serialize(state));return 0;
    }

#endif
    private static string Hash(string text)
    {
        using (SHA256 h = SHA256.Create())
            return BitConverter.ToString(h.ComputeHash(Encoding.UTF8.GetBytes(text))).Replace("-", "").ToLowerInvariant();
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool MoveFileEx(string existing, string target, int flags);

    // Write-ahead journal: temp file written through and flushed, then an atomic write-through replace.
    private static void Save(string path, Dictionary<string, object> state)
    {
        const int ReplaceExisting = 1, WriteThrough = 8;
        byte[] bytes = Encoding.UTF8.GetBytes(Program.Json.Serialize(state));
        string temp = path + ".new";
        using (FileStream f = new FileStream(temp, FileMode.Create, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
        { f.Write(bytes, 0, bytes.Length); f.Flush(true); }
        if (File.Exists(path)) File.Copy(path, path + ".previous", true);
        if (!MoveFileEx(temp, path, ReplaceExisting | WriteThrough))
            throw new IOException("Journal replace failed", Marshal.GetHRForLastWin32Error());
        using (FileStream f = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) f.Flush(true);
    }

#if ISOLATED_TEST
    private static List<Guid> CandidateIds(BusinessObjectsServer bos)
    {
        List<Guid> ids = new List<Guid>();
        using (SqlCommand q = new SqlCommand("SELECT TOP(1000) o.fISN FROM ORDERS o WHERE o.fSTATE=2 AND o.fDATE<=@day "
            + "AND o.fDATE>=DATEADD(day,-30,@day) AND NOT EXISTS(SELECT 1 FROM DOCPARENTS p WHERE p.fPARENTISN=o.fISN AND p.fDOCTYPE<>29) "
            + "ORDER BY o.fDATE DESC,o.fDOCNUM DESC", bos.Connection()))
        {
            q.Parameters.AddWithValue("@day", Day);
            using (SqlDataReader r = q.ExecuteReader()) while (r.Read()) ids.Add(r.GetGuid(0));
        }
        return ids;
    }

#endif
    private static decimal Stock(BusinessObjectsServer bos, int product, int? agent)
    {
        string sql = agent.HasValue
            ? "SELECT COALESCE(SUM(fQUANTITY),0) FROM HIRESTAGENTPRODUCTS WHERE fPRODUCTID=@product AND fAGENTID=@agent"
            : "SELECT COALESCE(SUM(fQUANTITY),0) FROM HIRESTSTORAGES WHERE fPRODUCTID=@product AND fSTORAGE=@warehouse";
        using (SqlCommand q = new SqlCommand(sql, bos.Connection()))
        {
            q.Parameters.AddWithValue("@product", product);
            if (agent.HasValue) q.Parameters.AddWithValue("@agent", agent.Value);
            else q.Parameters.AddWithValue("@warehouse", Warehouse);
            return Convert.ToDecimal(q.ExecuteScalar());
        }
    }

#if ISOLATED_TEST
    private static int Children(BusinessObjectsServer bos, Guid order)
    {
        using (SqlCommand q = new SqlCommand("SELECT COUNT(*) FROM DOCPARENTS WHERE fPARENTISN=@isn", bos.Connection()))
        { q.Parameters.AddWithValue("@isn", order); return Convert.ToInt32(q.ExecuteScalar()); }
    }

    private static HashSet<Guid> ClaimedOrders()
    {
        var ids=new HashSet<Guid>();
        foreach(string file in Directory.GetFiles(Path.Combine(Program.Root,"evidence"),"case-*.json"))
        {
            var state=Program.Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(file));
            if(state.ContainsKey("order_isn"))ids.Add(new Guid(Convert.ToString(state["order_isn"])));
            if(state.ContainsKey("orders"))foreach(var value in (System.Collections.IEnumerable)state["orders"])
            {var row=(Dictionary<string,object>)value;ids.Add(new Guid(Convert.ToString(row["order_isn"])));}
        }
        return ids;
    }

    private static Dictionary<string, object> Prepare(BusinessObjectsServer bos, string scenario,HashSet<Guid> extraClaims=null,string requiredWarehouse=null)
    {
        List<string> codes = new List<string>();
        using (SqlCommand q = new SqlCommand("SELECT fCODE FROM SALESAGENTS WHERE fCLOSED=0 AND fCODE LIKE N'B%' ORDER BY fCODE", bos.Connection()))
        using (SqlDataReader r = q.ExecuteReader()) while (r.Read()) codes.Add(r.GetString(0).Trim());
        string courier = null, car = null;
        int agentId = 0;
        using (SqlCommand q = new SqlCommand("SELECT TOP(1) fCODE FROM CARS WHERE fISCLOSED=0 "
            + "AND LEN(LTRIM(RTRIM(fCODE)))>0 ORDER BY CASE WHEN fCODE=N'333DN33' THEN 0 ELSE 1 END,fCODE", bos.Connection()))
            car = Convert.ToString(q.ExecuteScalar()).Trim();
        foreach (string code in codes)
        {
            var a = bos.Cache.SalesAgents.GetSalesAgentByCode(code);
            if (a == null) continue;
            courier = code; agentId = a.Id; break;
        }
        if (courier == null || String.IsNullOrWhiteSpace(car)) throw new Exception("No open ERP courier and explicit ERP car in the clone");
        List<string> warehouses=new List<string>();
        using(SqlCommand q=new SqlCommand("SELECT fCODE FROM STORAGES WHERE fCLOSE=0 AND fCODE NOT IN (N'006',N'011') ORDER BY fCODE",bos.Connection()))
        using(SqlDataReader r=q.ExecuteReader())while(r.Read())warehouses.Add(r.GetString(0).Trim());
        warehouses=warehouses.Where(w=>{var s=bos.Cache.Storages.GetStorageByCode(w);return s.IsAccessible&&!s.IsClosed&&s.Output;}).ToList();
        if(requiredWarehouse!=null)warehouses=warehouses.Where(w=>w==requiredWarehouse).ToList();
        HashSet<Guid> claimed=ClaimedOrders();if(extraClaims!=null)claimed.UnionWith(extraClaims);
        foreach (Guid id in CandidateIds(bos))
        {
            if(claimed.Contains(id))continue;
            SalesOrder order = SalesOrder.LoadInstance(bos, id);
            if (order.IsDraft || order.HasProviding || order.HasSale || order.ProductsList.Count == 0
                || order.Sum <= 0 || order.ExportedToTaxProgram) continue;
            bool simple = new[] { "full", "partial", "lost-response", "invalid-warehouse", "concurrent", "sql2025-smoke", "sql2025-smoke-ok" }.Contains(scenario);
            if (simple && (order.ProductsList.Count != 1 || order.GiftsList.Count != 0
                || order.OnDepositsList.Count != 0 || order.ExchangesList.Count != 0 || order.ProductsList[0].Quantity < (scenario.StartsWith("sql2025")?1:2))) continue;
            if (scenario=="multi" && order.ProductsList.Count<2) continue;
            if (scenario=="gift" && order.GiftsList.Count==0) continue;
            if (scenario=="deposit" && order.OnDepositsList.Count==0) continue;
            if (scenario=="exchange" && order.ExchangesList.Count==0) continue;
            if (scenario=="containers")
            {
                using(SqlCommand q=new SqlCommand("SELECT COUNT(*) FROM SALEDOCDETAILS d JOIN PRODUCTS p ON p.fID=d.fPRODUCTID WHERE d.fISN=@id AND p.fCONTAINER=1",bos.Connection()))
                { q.Parameters.AddWithValue("@id",id); if(Convert.ToInt32(q.ExecuteScalar())==0)continue; }
            }
            var row = order.ProductsList[0];
            var product = bos.Cache.Products.GetProductByCode(row.Code);
            order.VanAgentCode=courier;order.DeliveryCar=car;
            bool found=false;
            foreach(string warehouse in warehouses)
            {
                Warehouse=warehouse;
                ProductsProviding preview=MakeProvision(bos,order,scenario,courier,car);
                if(preview.ProductsList.Any(p=>new ReservationAccounting(bos).GetAvailableRem(Day,p.Code,Warehouse,order.ISN)<p.Quantity))continue;
                found=true;break;
            }
            if(!found)continue;
            decimal stock = Stock(bos, product.Id, null);
            return new Dictionary<string, object> {
                {"scenario",scenario},{"status","planned"},{"database",Program.Database},
                {"order_isn",id.ToString().ToUpperInvariant()},{"order_num",order.DocNum},
                {"order_body_sha256",BodyHash(bos,id)},{"source_sum",order.Sum},
                {"product_id",product.Id},{"product_code",row.Code},{"ordered_quantity",row.Quantity},
                {"courier",courier},{"agent_id",agentId},{"car",car},{"warehouse",Warehouse},
                {"warehouse_before",stock},{"agent_before",Stock(bos,product.Id,agentId)},
                {"native_available_for_order",new ReservationAccounting(bos).GetAvailableRem(Day,row.Code,Warehouse,order.ISN)},
                {"source_product_lines",order.ProductsList.Count},{"source_gift_lines",order.GiftsList.Count},
                {"source_deposit_lines",order.OnDepositsList.Count},{"source_exchange_lines",order.ExchangesList.Count},
                {"sales_before",Convert.ToInt64(Program.Scalar(bos.Connection(),"SELECT COUNT_BIG(*) FROM SALES"))}
            };
        }
        throw new Exception("No suitable unprocessed order with available warehouse stock for scenario " + scenario + "; no writes made");
    }

#endif
    private static decimal BatchAvailable(BusinessObjectsServer bos,string code,IEnumerable<SalesOrder> orders)
    {
        ReservationAccounting acc=new ReservationAccounting(bos);
        decimal free=acc.GetAvailableRem(Day,code,Warehouse,null),available=free;
        foreach(SalesOrder order in orders)available+=acc.GetAvailableRem(Day,code,Warehouse,order.ISN)-free;
        return available;
    }

#if ISOLATED_TEST
    private static int Batch(BusinessObjectsServer bos,string scenario)
    {
        string path=Path.Combine(Program.Root,"evidence","case-"+scenario+".json");
        if(File.Exists(path))
        {
            var previous=Program.Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path));
            var recheck=new Dictionary<string,object>{{"scenario",scenario},{"previous_status",previous["status"]},
                {"replay_dispatched",false},{"sales_now",Convert.ToInt64(Program.Scalar(bos.Connection(),"SELECT COUNT_BIG(*) FROM SALES"))}};
            if(previous.ContainsKey("provision_isn"))
            {
                using(SqlCommand q=new SqlCommand("SELECT COUNT(*) FROM DOCUMENTS WHERE fISN=@id",bos.Connection()))
                {q.Parameters.AddWithValue("@id",new Guid(Convert.ToString(previous["provision_isn"])));recheck["provision_exists"]=Convert.ToInt32(q.ExecuteScalar())==1;}
            }
            Save(Path.Combine(Program.Root,"evidence","recheck-"+scenario+".json"),recheck);
            Console.WriteLine(Program.Json.Serialize(recheck));return 0;
        }
        var a=Prepare(bos,"full");var b=Prepare(bos,"full",new HashSet<Guid>{new Guid(Convert.ToString(a["order_isn"]))},Convert.ToString(a["warehouse"]));
        if(!Equals(a["warehouse"],b["warehouse"])||!Equals(a["car"],b["car"])||!Equals(a["courier"],b["courier"]))
            throw new Exception("Batch fixtures must share one explicit warehouse/courier/car");
        Warehouse=Convert.ToString(a["warehouse"]);int agent=Convert.ToInt32(a["agent_id"]);
        var plans=new[]{a,b};var source=new List<SalesOrder>();
        foreach(var p in plans)
        {
            Guid id=new Guid(Convert.ToString(p["order_isn"]));
            if(BodyHash(bos,id)!=Convert.ToString(p["order_body_sha256"]))throw new Exception("Batch source changed");
            SalesOrder order=SalesOrder.LoadInstance(bos,id);order.VanAgentCode=Convert.ToString(p["courier"]);order.DeliveryCar=Convert.ToString(p["car"]);source.Add(order);
        }
        ProductsProviding provision=MakeProvision(bos,source[0],scenario,Convert.ToString(a["courier"]),Convert.ToString(a["car"]));
        ProductsProviding.AddSalesOrderToDeliveriesList(bos,provision,provision.DeliveriesList,source[1],false);
        provision.ReloadProductsGridByDelivery(false,source[0].PriceListType);
        var stocks=new List<Dictionary<string,object>>();
        foreach(var row in provision.ProductsList)
        {
            int id=bos.Cache.Products.GetProductByCode(row.Code).Id;decimal before=Stock(bos,id,null);
            decimal available=BatchAvailable(bos,row.Code,source);
            if(available<row.Quantity)throw new Exception("Combined batch quantity exceeds native unreserved availability; no native Store dispatched");
            stocks.Add(new Dictionary<string,object>{{"product_id",id},{"product_code",row.Code},{"quantity",row.Quantity},
                {"warehouse_before",before},{"native_available_for_orders",available},{"agent_before",Stock(bos,id,agent)}});
        }
        var state=new Dictionary<string,object>{{"scenario",scenario},{"status","assignments-pending"},{"database",Program.Database},
            {"orders",plans},{"warehouse",Warehouse},{"car",a["car"]},{"courier",a["courier"]},{"stock_checks",stocks},
            {"sales_before",Convert.ToInt64(Program.Scalar(bos.Connection(),"SELECT COUNT_BIG(*) FROM SALES"))}};
        Save(path,state);
        foreach(SalesOrder order in source)order.Store(new StoreRequest());
        foreach(var row in provision.ProductsList)
            if(BatchAvailable(bos,row.Code,source)<row.Quantity)throw new Exception("Availability changed after assignments; no providing or sale dispatched");
        state["provision_isn"]=provision.ISN.ToString();state["status"]="provision-pending";Save(path,state);
        provision.Store(new StoreRequest{MakeDraft=false});provision=ProductsProviding.LoadInstance(bos,provision.ISN);
        if(provision.IsDraft||source.Any(o=>!provision.Parents.ContainsKey(o.ISN)))throw new Exception("Batch providing parent links not verified");
        foreach(var row in stocks)
        {
            int id=Convert.ToInt32(row["product_id"]);decimal qty=Convert.ToDecimal(row["quantity"]);
            row["warehouse_after_provision"]=Stock(bos,id,null);row["agent_after_provision"]=Stock(bos,id,agent);
            if(Convert.ToDecimal(row["warehouse_before"])-Stock(bos,id,null)!=qty||Stock(bos,id,agent)-Convert.ToDecimal(row["agent_before"])!=qty)
                throw new Exception("Batch providing stock mismatch");
        }
        var sales=new List<Dictionary<string,object>>();state["sales"]=sales;
        foreach(SalesOrder original in source)
        {
            SalesOrder order=SalesOrder.LoadInstance(bos,original.ISN);Sale sale=BuildSale(bos,order,provision);
            var result=new Dictionary<string,object>{{"order_isn",order.ISN.ToString()},{"sale_isn",sale.ISN.ToString()},{"status","sale-pending"}};
            sales.Add(result);state["status"]="sales-pending";Save(path,state);
            sale.Store(new StoreRequest{MakeDraft=false});sale=Sale.LoadInstance(bos,sale.ISN);
            if(sale.IsDraft||!sale.Parents.ContainsKey(order.ISN)||sale.Sum!=order.Sum||sale.DeliveryCar!=provision.Car)
                throw new Exception("Batch sale identity/amount mismatch");
            using(SqlCommand q=new SqlCommand("SELECT COALESCE(SUM(CASE WHEN fDBCR='D' THEN fSUM ELSE -fSUM END),0) FROM HICUSTOMERSDEBT WHERE fBASE=@id",bos.Connection()))
            {q.Parameters.AddWithValue("@id",sale.ISN);if(Convert.ToDecimal(q.ExecuteScalar())!=sale.Sum)throw new Exception("Batch debt mismatch");}
            result["status"]="verified";result["sale_num"]=sale.DocNum;result["sum"]=sale.Sum;Save(path,state);
        }
        foreach(var row in stocks)
        {
            int id=Convert.ToInt32(row["product_id"]);row["agent_after_sales"]=Stock(bos,id,agent);
            if(Stock(bos,id,agent)!=Convert.ToDecimal(row["agent_before"]))throw new Exception("Batch final agent stock mismatch");
        }
        long after=Convert.ToInt64(Program.Scalar(bos.Connection(),"SELECT COUNT_BIG(*) FROM SALES"));
        if(after-Convert.ToInt64(state["sales_before"])!=2)throw new Exception("Batch sales count is not two");
        state["sales_after"]=after;state["provision_num"]=provision.DocNum;state["status"]="complete";Save(path,state);
        Console.WriteLine(Program.Json.Serialize(state));return 0;
    }

#endif
    private static ProductsProviding MakeProvision(BusinessObjectsServer bos,SalesOrder order,string scenario,string courier,string car)
    {
        ProductsProviding p=new ProductsProviding(bos){DocDate=Day,Storage=Warehouse,SalesAgentCode=courier,Car=car,
            PricelistType=order.PriceListType,Surrender=bos.Cache.Storages.GetStorageByCode(Warehouse).Stockkeeper,
            Comment=Program.DocumentCommentPrefix+scenario,ExportedToTaxProgram=false};
        if(!ProductsProviding.AddSalesOrderToDeliveriesList(bos,p,p.DeliveriesList,order,false))throw new Exception("Empty native delivery list");
#if ISOLATED_TEST
        if(scenario=="partial")foreach(var row in p.DeliveriesList)row.Quantity=Decimal.Floor(row.Quantity/2m);
#endif
        p.ReloadProductsGridByDelivery(false,order.PriceListType);
        return p;
    }

#if ISOLATED_TEST
    private static string BodyHash(BusinessObjectsServer bos, Guid id)
    {
        using (SqlCommand q = new SqlCommand("SELECT fBODY FROM DOCUMENTS WHERE fISN=@id", bos.Connection()))
        { q.Parameters.AddWithValue("@id", id); return Hash(Convert.ToString(q.ExecuteScalar())); }
    }

    private static int Execute(BusinessObjectsServer bos, Dictionary<string, object> state, string path)
    {
        Guid orderId = new Guid(Convert.ToString(state["order_isn"]));
        int nonReservations;
        using(SqlCommand q=new SqlCommand("SELECT COUNT(*) FROM DOCPARENTS WHERE fPARENTISN=@id AND fDOCTYPE<>29",bos.Connection()))
        {q.Parameters.AddWithValue("@id",orderId);nonReservations=Convert.ToInt32(q.ExecuteScalar());}
        if (nonReservations != 0 || BodyHash(bos, orderId) != Convert.ToString(state["order_body_sha256"]))
            throw new Exception("Source order changed after preview; no execution");
        SalesOrder order = SalesOrder.LoadInstance(bos, orderId);
        string scenario = Convert.ToString(state["scenario"]);
        string courier = Convert.ToString(state["courier"]), car = Convert.ToString(state["car"]);
        int productId = Convert.ToInt32(state["product_id"]), agentId = Convert.ToInt32(state["agent_id"]);
        try
        {
            state["status"] = "assignment-pending"; Save(path,state);
            order.VanAgentCode = courier; order.DeliveryCar = car;
            order.Store(new StoreRequest());
            order = SalesOrder.LoadInstance(bos,orderId);
            if (order.VanAgentCode != courier || order.DeliveryCar != car) throw new Exception("Assignment readback mismatch");
            state["assignment_verified"] = true;
            ProductsProviding provision = MakeProvision(bos,order,scenario,courier,car);
            var stockChecks=new List<Dictionary<string,object>>();
            foreach(var product in provision.ProductsList)
            {
                int resolvedId=bos.Cache.Products.GetProductByCode(product.Code).Id;
                stockChecks.Add(new Dictionary<string,object>{{"product_id",resolvedId},{"product_code",product.Code},
                    {"provided_quantity",product.Quantity},{"warehouse_before",Stock(bos,resolvedId,null)},
                    {"agent_before",Stock(bos,resolvedId,agentId)}});
            }
            state["stock_checks"]=stockChecks;
            if (scenario == "invalid-warehouse") provision.Storage="INVALID_TEST_STORAGE";
            state["provision_isn"]=provision.ISN.ToString().ToUpperInvariant();
            state["status"]="provision-pending"; Save(path,state);
            provision.Store(new StoreRequest { MakeDraft=false });
            if (scenario == "lost-response") throw new TimeoutException("Simulated lost response after native providing Store");
            provision=ProductsProviding.LoadInstance(bos,provision.ISN);
            if (provision.IsDraft || provision.Car != car || provision.SalesAgentCode != courier
                || !provision.Parents.ContainsKey(orderId)) throw new Exception("Providing identity/link readback mismatch");
            state["provision_num"]=provision.DocNum;
            state["warehouse_after_provision"]=Stock(bos,productId,null);
            state["agent_after_provision"]=Stock(bos,productId,agentId);
            decimal delivered=provision.DeliveriesList.Where(r=>r.Code==Convert.ToString(state["product_code"])).Sum(r=>r.Quantity);
            foreach(var check in stockChecks)
            {
                int pid=Convert.ToInt32(check["product_id"]);decimal qty=Convert.ToDecimal(check["provided_quantity"]);
                check["warehouse_after_provision"]=Stock(bos,pid,null);check["agent_after_provision"]=Stock(bos,pid,agentId);
                if(Convert.ToDecimal(check["warehouse_before"])-Stock(bos,pid,null)!=qty
                    ||Stock(bos,pid,agentId)-Convert.ToDecimal(check["agent_before"])!=qty)
                    throw new Exception("Native stock movements did not match the provided quantity");
            }
            state["delivered_quantity"]=delivered;
            Sale sale=BuildSale(bos,order,provision);
            state["sale_isn"]=sale.ISN.ToString().ToUpperInvariant();
            state["status"]="sale-pending"; Save(path,state);
            sale.Store(new StoreRequest { MakeDraft=false });
            sale=Sale.LoadInstance(bos,sale.ISN);
            if (sale.IsDraft || !sale.Parents.ContainsKey(orderId) || sale.VanAgentCode!=courier || sale.DeliveryCar!=car)
                throw new Exception("Sale identity/order link readback mismatch");
            foreach(var group in provision.DeliveriesList.GroupBy(r=>r.Code+"|"+r.Operation))
            {
                var row=group.First();decimal expected=group.Sum(r=>r.Quantity);
                decimal actual=row.Operation==SOrderProductsOperationSE.Sale?sale.ProductsList.Where(r=>r.Code==row.Code).Sum(r=>r.Quantity):
                    row.Operation==SOrderProductsOperationSE.Gift?sale.GiftsList.Where(r=>r.Code==row.Code).Sum(r=>r.Quantity):
                    row.Operation==SOrderProductsOperationSE.Depositing?sale.OnDepositsList.Where(r=>r.Code==row.Code).Sum(r=>r.Quantity):
                    sale.ExchangesList.Where(r=>r.Code==row.Code).Sum(r=>r.Quantity);
                if(actual!=expected)throw new Exception("Sale operation/quantity differs from the exact providing");
            }
            if (scenario!="partial" && sale.Sum!=Convert.ToDecimal(state["source_sum"])) throw new Exception("Full-sale amount changed");
            state["sale_num"]=sale.DocNum;state["sale_sum"]=sale.Sum;
            state["agent_after_sale"]=Stock(bos,productId,agentId);
            foreach(var check in stockChecks)
            {
                int pid=Convert.ToInt32(check["product_id"]);check["agent_after_sale"]=Stock(bos,pid,agentId);
                if(Stock(bos,pid,agentId)!=Convert.ToDecimal(check["agent_before"]))throw new Exception("Agent stock did not return to its original quantity after sale");
            }
            using(SqlCommand q=new SqlCommand("SELECT COALESCE(SUM(CASE WHEN fDBCR='D' THEN fSUM ELSE -fSUM END),0) FROM HICUSTOMERSDEBT WHERE fBASE=@id",bos.Connection()))
            {q.Parameters.AddWithValue("@id",sale.ISN);state["sale_debt_movement"]=Convert.ToDecimal(q.ExecuteScalar());}
            if(Convert.ToDecimal(state["sale_debt_movement"])!=sale.Sum)throw new Exception("Sale debt movement does not match its amount");
            state["status"]="complete";Save(path,state);
            Console.WriteLine(Program.Json.Serialize(state));return 0;
        }
        catch(Exception ex)
        {
            state["error_type"]=ex.GetType().Name;state["error"]=ex.Message;
            state["sales_after_error"]=Convert.ToInt64(Program.Scalar(bos.Connection(),"SELECT COUNT_BIG(*) FROM SALES"));
            state["child_documents_after_error"]=Children(bos,orderId);
            state["stopped_without_automatic_retry"]=true;Save(path,state);
            Console.WriteLine(Program.Json.Serialize(state));throw;
        }
    }

#endif
    private static Sale BuildSale(BusinessObjectsServer bos, SalesOrder order, ProductsProviding provision)
    {
        var deliveries=bos.CS().GetSalesOrderDeliveredQuantity(order.DocNum,provision.DocNum);
        if(deliveries==null || deliveries.Count==0)throw new Exception("No registered native providing quantities");
        Sale sale=new Sale(bos){DocDate=Day,Division=order.Division,SalesAgentCode=order.SalesAgentCode,
            SalesArea=order.SalesArea,VanAgentCode=provision.SalesAgentCode,DeliveryCar=provision.Car,
            CustomerCode=order.CustomerCode,PriceListType=order.PriceListType,DocGroup=order.DocGroup,
            PaymentType=order.PaymentType,DelayDays=order.DelayDays,OrganizationAccount=order.OrganizationAccount,
            Contact=order.Contact,Comment=order.Comment,OtherSystemSendStatus="2",ExportedToTaxProgram=order.ExportedToTaxProgram,
            TaxSeriaNumber=order.TaxSeriaNumber,SubmitDate=order.SubmitDate,CoSignDate=order.CoSignDate,
            AdditionalDiscount=order.AdditionalDiscount,
            DeliveryAddress=new SaleBase.DeliveryAddressInfo(order.DeliveryAddress.Id,order.DeliveryAddress.Address,
                order.DeliveryAddress.AdditionalInfo,order.DeliveryAddress.Latitude,order.DeliveryAddress.Longitude)};
        sale.Parents.Add(order.ISN,DocumentType.SalesOrderDoc);
        foreach(var item in deliveries)
        {
            var product=bos.Cache.Products.GetProductByCode(item.Product);
            if(item.Operation==SOrderProductsOperationSE.Gift)
            {
                sale.GiftsList.Add(new SaleBase.GiftRow(sale){Code=item.Product,Name=item.ProductName,MeasureUnit=item.MeasureUnit,
                    Quantity=item.Quantity,AdditionalUnitQuantity=bos.CS().CalculateProductAdditionalUnitQuantity(item.Quantity,product)});
                continue;
            }
            if(item.Operation==SOrderProductsOperationSE.Depositing)
            {
                sale.OnDepositsList.Add(new SaleBase.OnDepositRow(sale){Code=item.Product,Name=item.ProductName,MeasureUnit=item.MeasureUnit,
                    Quantity=item.Quantity,AdditionalUnitQuantity=bos.CS().CalculateProductAdditionalUnitQuantity(item.Quantity,product)});
                continue;
            }
            if(item.Operation==SOrderProductsOperationSE.Exchange)
            {
                sale.ExchangesList.Add(new SaleBase.ExchangeRow(sale){Code=item.Product,Name=item.ProductName,MeasureUnit=item.MeasureUnit,
                    Quantity=item.Quantity,AdditionalUnitQuantity=bos.CS().CalculateProductAdditionalUnitQuantity(item.Quantity,product)});
                continue;
            }
            if(item.Operation!=SOrderProductsOperationSE.Sale)throw new Exception("Unknown native delivery operation");
            SaleBase.SalesRow row=new SaleBase.SalesRow(sale){Code=item.Product,Name=item.ProductName,
                MeasureUnit=item.MeasureUnit,Quantity=item.Quantity,
                AdditionalUnitQuantity=bos.CS().CalculateProductAdditionalUnitQuantity(item.Quantity,product),
                Price=item.Price,Discount=item.DiscountPercent,DiscountedPrice=item.DiscountedPrice,
                PriceListPrice=item.PriceListPrice,Sum=(item.Quantity*item.DiscountedPrice).RoundAs(CommonConst.LenSumPrecision(bos))};
            row.CustomerDiscount=row.CalculateCustomerDiscount();
            decimal vat=0,excise=0,fee=0;
            bos.CS().CalculateProductTaxes(ref vat,ref excise,ref fee,Day,product.Id,order.CustomerId,row.Quantity,row.Sum);
            row.IncludedVAT=vat;row.IncludedExcise=excise;row.IncludedEnvironmentalFee=fee;
            sale.ProductsList.Add(row);
        }
        sale.SetTotals();
        if(sale.UndiscountedSum==order.UndiscountedSum)sale.Sum=order.Sum;
        return sale;
    }

#if ISOLATED_TEST
    private static int Recheck(BusinessObjectsServer bos, Dictionary<string,object> state,string path)
    {
        Guid order=new Guid(Convert.ToString(state["order_isn"]));
        long sales=Convert.ToInt64(Program.Scalar(bos.Connection(),"SELECT COUNT_BIG(*) FROM SALES"));
        var result=new Dictionary<string,object>{{"scenario",state["scenario"]},{"previous_status",state["status"]},
            {"replay_dispatched",false},{"sales_now",sales},{"child_documents_now",Children(bos,order)}};
        if(Convert.ToString(state["status"])=="complete")
        {
            Sale sale=Sale.LoadInstance(bos,new Guid(Convert.ToString(state["sale_isn"])));
            ProductsProviding p=ProductsProviding.LoadInstance(bos,new Guid(Convert.ToString(state["provision_isn"])));
            result["existing_documents_verified"]=sale.Parents.ContainsKey(order)&&p.Parents.ContainsKey(order)&&!sale.IsDraft&&!p.IsDraft;
        }
        else if(state.ContainsKey("provision_isn"))
        {
            Guid pid=new Guid(Convert.ToString(state["provision_isn"]));int exists;
            using(SqlCommand q=new SqlCommand("SELECT COUNT(*) FROM DOCUMENTS WHERE fISN=@id",bos.Connection()))
            {q.Parameters.AddWithValue("@id",pid);exists=Convert.ToInt32(q.ExecuteScalar());}
            result["provision_exists"]=exists==1;
            if(exists==1)
            {
                ProductsProviding p=ProductsProviding.LoadInstance(bos,pid);
                result["provision_registered_and_linked"]=!p.IsDraft&&p.Parents.ContainsKey(order);
                result["provision_num"]=p.DocNum;
            }
            using(SqlCommand q=new SqlCommand("SELECT COUNT(*) FROM DOCPARENTS WHERE fPARENTISN=@id AND fDOCTYPE=2",bos.Connection()))
            {q.Parameters.AddWithValue("@id",order);result["sales_for_order"]=Convert.ToInt32(q.ExecuteScalar());}
        }
        string output=Path.Combine(Program.Root,"evidence","recheck-"+state["scenario"]+".json");Save(output,result);
        Console.WriteLine(Program.Json.Serialize(result));return 0;
    }
#endif
}
