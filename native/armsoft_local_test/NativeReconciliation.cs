// Сверка существующего предоставления и явное создание недостающих продаж; цель задаёт Program.
using System;
using System.Collections.Generic;
using System.Data.SqlClient;
using System.Globalization;
using System.IO;
using System.Linq;
using ArmSoft.Core;
using ArmSoft.SalesManagement.BusinessObjects;
using ArmSoft.SalesManagement.BusinessObjects.Documents;

internal static partial class NativeCases
{
#if ISOLATED_TEST
    // Одноразовые fault-fixture файлы принимаются только жёстко закреплённым локальным harness.
    private static void TestStopOnce(string name)
    {
        string path=Path.Combine(Program.Root,"harness",name);
        if(File.Exists(path)){File.Delete(path);throw new TimeoutException("Isolated recovery fixture: "+name);}
    }
#else
    // В production-сборке компилятор удаляет вызовы вместе со строковыми аргументами.
    [System.Diagnostics.Conditional("ISOLATED_TEST")]
    private static void TestStopOnce(string name) {}
#endif
    private static string DocStamp(BusinessObjectsServer bos,Guid id,int kind)
    {
        using(var q=new SqlCommand("SELECT fTS FROM DOCUMENTS WHERE fISN=@id AND fDOCTYPE=@kind AND fDOCSTATE=2",bos.Connection()))
        {q.Parameters.AddWithValue("@id",id);q.Parameters.AddWithValue("@kind",kind);object value=q.ExecuteScalar();Require(value is byte[],"unregistered");return Convert.ToBase64String((byte[])value);}
    }

    private static bool Exists(BusinessObjectsServer bos,Guid id)
    {
        using(var q=new SqlCommand("SELECT COUNT(*) FROM DOCUMENTS WHERE fISN=@id",bos.Connection()))
        {q.Parameters.AddWithValue("@id",id);return Convert.ToInt32(q.ExecuteScalar())!=0;}
    }

    private static void VerifyRecoverySale(BusinessObjectsServer bos,Dictionary<string,object> source,Dictionary<string,object> load,Guid saleId,Guid providing)
    {
        var sale=Sale.LoadInstance(bos,saleId);
        Require(!sale.IsDraft&&sale.Parents.ContainsKey(new Guid(Text(source,"isn")))&&sale.Sum==Convert.ToDecimal(source["sum"])
            &&sale.VanAgentCode==Text(load,"courier")&&sale.DeliveryCar==Text(load,"car")&&!sale.ExportedToTaxProgram,"verification");
        Require(ExactSaleQuantities(bos,saleId,providing,Text(source,"number")),"verification");
        using(var q=new SqlCommand("SELECT COALESCE(SUM(CASE WHEN fDBCR='D' THEN fSUM ELSE -fSUM END),0) FROM HICUSTOMERSDEBT WHERE fBASE=@id",bos.Connection()))
        {q.Parameters.AddWithValue("@id",saleId);Require(Convert.ToDecimal(q.ExecuteScalar())==sale.Sum,"verification");}
    }

    private static Dictionary<string,object> InspectRecovery(BusinessObjectsServer bos,string id)
    {
        string path=Path.Combine(DispatchRoot,"operation-"+id+".json");Require(File.Exists(path),"not_found");
        string original=File.ReadAllText(path);var op=Map(Program.Json.DeserializeObject(original));
        Require(Text(op,"status")!="complete","already_complete");
        Day=DateTime.ParseExact(Text(op,"day"),"yyyy-MM-dd",CultureInfo.InvariantCulture);
        Require(Day==TimeZoneInfo.ConvertTimeBySystemTimeZoneId(DateTime.UtcNow,"Caucasus Standard Time").Date,"past");
        var missing=new List<Dictionary<string,object>>();var proofs=new List<object>();var steps=Maps(op["steps"]);
        foreach(var load in Maps(op["loads"]))
        {
            var sources=Maps(load["orders"]);var sourceIds=sources.Select(s=>new Guid(Text(s,"isn"))).ToList();
            var provisionSteps=steps.Where(s=>Text(s,"kind")=="providing"&&Strings(s["order_isns"]).Any(x=>sourceIds.Contains(new Guid(x)))).ToList();
            Require(provisionSteps.Count==1,"providing_missing");var provisionStep=provisionSteps[0];
            Guid providingId=new Guid(Text(provisionStep,"isn"));string providingStamp=DocStamp(bos,providingId,19);
            var providing=ProductsProviding.LoadInstance(bos,providingId);
            Require(!providing.IsDraft&&providing.Storage==Text(load,"warehouse")&&providing.Car==Text(load,"car")
                &&providing.SalesAgentCode==Text(load,"courier")&&!providing.ExportedToTaxProgram,"verification");
            Require(providing.Parents.Count==sourceIds.Count&&sourceIds.All(o=>providing.Parents.ContainsKey(o)),"foreign_child");
            Require(Hash(Program.Json.Serialize(DeliveryManifest(providing)))==Hash(Program.Json.Serialize(load["manifest"])),"changed");
            foreach(var source in sources)
            {
                Guid orderId=new Guid(Text(source,"isn"));var order=SalesOrder.LoadInstance(bos,orderId);
                DocStamp(bos,orderId,1);
                Require(!order.IsDraft&&ContentStamp(bos,orderId)==Text(source,"content")&&order.DocNum==Text(source,"number")
                    &&order.Sum==Convert.ToDecimal(source["sum"])&&order.VanAgentCode==Text(load,"courier")
                    &&order.DeliveryCar==Text(load,"car")&&!order.ExportedToTaxProgram&&order.ExchangesList.Count==0,"changed");
                var sales=new List<Guid>();
                using(var q=new SqlCommand("SELECT fISN,fDOCTYPE FROM DOCPARENTS WHERE fPARENTISN=@id AND fDOCTYPE<>29",bos.Connection()))
                {
                    q.Parameters.AddWithValue("@id",orderId);
                    using(var r=q.ExecuteReader())while(r.Read())
                    {Guid child=r.GetGuid(0);int kind=Convert.ToInt32(r.GetValue(1));if(kind==2)sales.Add(child);else Require(kind==19&&child==providingId,"foreign_child");}
                }
                Require(sales.Count<=1,"foreign_child");
                var saleSteps=steps.Where(s=>Text(s,"kind")=="sale"&&new Guid(Text(s,"order_isn"))==orderId).ToList();
                foreach(var absent in saleSteps.Where(s=>Text(s,"status")=="confirmed_absent"))Require(!Exists(bos,new Guid(Text(absent,"isn"))),"foreign_child");
                string saleStamp="";
                if(sales.Count==1)
                {
                    Require(saleSteps.Any(s=>new Guid(Text(s,"isn"))==sales[0]&&Text(s,"status")!="confirmed_absent"),"foreign_child");
                    saleStamp=DocStamp(bos,sales[0],2);VerifyRecoverySale(bos,source,load,sales[0],providingId);
                }
                else
                {
                    foreach(var prior in saleSteps)Require(!Exists(bos,new Guid(Text(prior,"isn"))),"foreign_child");
                    missing.Add(new Dictionary<string,object>{{"order_isn",orderId.ToString()},{"number",order.DocNum},{"sum",order.Sum},
                        {"providing_isn",providingId.ToString()},{"providing_number",providing.DocNum},{"car",providing.Car}});
                }
                proofs.Add(new{order=orderId,version=Version(bos,orderId),content=ContentStamp(bos,orderId),providing=providingId,
                    providing_version=providingStamp,sale=sales.Count==1?sales[0].ToString():"",sale_version=saleStamp});
            }
        }
        return new Dictionary<string,object>{{"id",id},{"database",Program.Database},{"environment",Program.EnvironmentName},
            {"status","reconciliation_preview"},{"missing_sales",missing},{"missing_count",missing.Count},
            {"signature",Hash(original+Program.Json.Serialize(proofs))},{"can_resume",true},{"no_write_dispatched",true}};
    }

    private static object RecoveryPreview(BusinessObjectsServer bos,string id)
    {
        var result=InspectRecovery(bos,id);string reviewId=Guid.NewGuid().ToString("D");
        result["review_id"]=reviewId;result["created_utc"]=DateTime.UtcNow.ToString("O");
        Save(Path.Combine(DispatchRoot,"reconciliation-"+reviewId+".json"),result);return result;
    }

    private static object RecoveryExecute(BusinessObjectsServer bos,string id,string reviewId,string actor,string reason)
    {
        string attempt=Path.Combine(DispatchRoot,"reconciliation-attempt-"+reviewId+".json");
        if(File.Exists(attempt))return DispatchStatus(bos,id);
        Require(!File.Exists(Path.Combine(Program.Root,"QUARANTINED.json")),"quarantined");
        int maxOrders=WriteGateMaxOrders();Require(maxOrders>0,"writes_disabled");
        Require(!String.IsNullOrWhiteSpace(actor)&&actor.Length<=100&&!String.IsNullOrWhiteSpace(reason)&&reason.Trim().Length>=10&&reason.Length<=500,"bad_request");
        string reviewPath=Path.Combine(DispatchRoot,"reconciliation-"+reviewId+".json");Require(File.Exists(reviewPath),"not_found");
        var review=Map(Program.Json.DeserializeObject(File.ReadAllText(reviewPath)));Require(Text(review,"id")==id,"not_found");
        Require(DateTime.UtcNow-DateTime.Parse(Text(review,"created_utc"),CultureInfo.InvariantCulture,DateTimeStyles.RoundtripKind)<TimeSpan.FromMinutes(5),"expired");
        var fresh=InspectRecovery(bos,id);Require(Text(fresh,"signature")==Text(review,"signature"),"changed");
        Require(Convert.ToInt32(fresh["missing_count"])<=maxOrders,"too_many");
        var audit=new Dictionary<string,object>{{"id",id},{"review_id",reviewId},{"actor",actor},{"reason",reason.Trim()},
            {"created_utc",DateTime.UtcNow.ToString("O")},{"status","running"},{"missing_before",fresh["missing_sales"]}};
        Save(attempt,audit);
        string path=Path.Combine(DispatchRoot,"operation-"+id+".json");var op=Map(Program.Json.DeserializeObject(File.ReadAllText(path)));
        var steps=Maps(op["steps"]);op["steps"]=steps;bool dispatched=false;
        try
        {
            foreach(var item in Maps(fresh["missing_sales"]))
            {
                Guid orderId=new Guid(Text(item,"order_isn"));Guid providingId=new Guid(Text(item,"providing_isn"));
                var load=Maps(op["loads"]).Single(l=>Maps(l["orders"]).Any(s=>new Guid(Text(s,"isn"))==orderId));
                var source=Maps(load["orders"]).Single(s=>new Guid(Text(s,"isn"))==orderId);
                using(var q=new SqlCommand("SELECT COUNT(*) FROM DOCPARENTS WHERE fPARENTISN=@id AND fDOCTYPE<>29 AND fISN<>@providing",bos.Connection()))
                {q.Parameters.AddWithValue("@id",orderId);q.Parameters.AddWithValue("@providing",providingId);Require(Convert.ToInt32(q.ExecuteScalar())==0,"changed");}
                Require(ContentStamp(bos,orderId)==Text(source,"content"),"changed");
                foreach(var prior in steps.Where(s=>Text(s,"kind")=="sale"&&new Guid(Text(s,"order_isn"))==orderId))
                {Require(!Exists(bos,new Guid(Text(prior,"isn"))),"foreign_child");prior["status"]="confirmed_absent";prior["absence_review_id"]=reviewId;}
                var sale=BuildSale(bos,SalesOrder.LoadInstance(bos,orderId),ProductsProviding.LoadInstance(bos,providingId));
                var step=new Dictionary<string,object>{{"kind","sale"},{"isn",sale.ISN.ToString()},{"order_isn",orderId.ToString()},
                    {"providing_isn",providingId.ToString()},{"status","pending"},{"reconciliation_review_id",reviewId}};
                steps.Add(step);Save(path,op);dispatched=true;sale.Store(new StoreRequest{MakeDraft=false});
                TestStopOnce("test-stop-after-recovery-sale.once");
                VerifyRecoverySale(bos,source,load,sale.ISN,providingId);DocStamp(bos,sale.ISN,2);
                sale=Sale.LoadInstance(bos,sale.ISN);step["number"]=sale.DocNum;step["sum"]=sale.Sum;step["status"]="verified";Save(path,op);dispatched=false;
            }
            Require(Convert.ToInt32(InspectRecovery(bos,id)["missing_count"])==0,"verification");
            foreach(var step in steps.Where(s=>Text(s,"status")!="confirmed_absent"))
            {
                step["reconciled_from_status"]=step["status"];step["reconciliation_review_id"]=reviewId;step["status"]="verified";
                if(Text(step,"kind")!="assignment")
                using(var q=new SqlCommand("SELECT fDOCNUM FROM DOCUMENTS WHERE fISN=@id AND fDOCSTATE=2",bos.Connection()))
                {q.Parameters.AddWithValue("@id",new Guid(Text(step,"isn")));step["number"]=Convert.ToString(q.ExecuteScalar());}
            }
            op["previous_status"]=op["status"];op["status"]="complete";op["reconciliation_review_id"]=reviewId;Save(path,op);
            audit["status"]="complete";Save(attempt,audit);
        }
        catch(Exception ex)
        {op["status"]=dispatched?"unknown":"partial";op["error_code"]=ex is DispatchInvalid?ex.Message:"native_error";Save(path,op);audit["status"]=op["status"];Save(attempt,audit);}
        return DispatchStatus(bos,id);
    }
}
