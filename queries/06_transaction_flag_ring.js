// Marcação da rede inteira em uma transação ACID multi-documento.
// Espelha app/db/investigation.py; existe aqui para ser lido sem subir o backend.
//
//   mongosh "$MONGODB_URI" queries/06_transaction_flag_ring.js

db = db.getSiblingDB(process.env.MONGODB_DB || "graph_fraud_ring");

const ring = db.rings.findOne();
const membros = db.people.find({ ring_id: ring.ring_id }, { _id: 1 }).toArray().map((d) => d._id);
const caseId = `case_shell_${new Date().getTime()}`;

const session = db.getMongo().startSession();
const t0 = Date.now();
session.startTransaction({ readConcern: { level: "snapshot" }, writeConcern: { w: "majority" } });
try {
  const sdb = session.getDatabase(db.getName());
  const contas = sdb.accounts.updateMany(
    { person_id: { $in: membros } },
    { $set: { status: "under_investigation", case_id: caseId, flagged_at: new Date() } }
  );
  const pessoas = sdb.people.updateMany(
    { _id: { $in: membros } },
    { $addToSet: { risk_flags: "under_investigation" }, $set: { case_id: caseId } }
  );
  sdb.investigations.insertOne({
    _id: caseId,
    person_ids: membros,
    reason: "rede identificada por traversal (mongosh)",
    opened_at: new Date(),
    status: "open",
  });
  session.commitTransaction();
  print(`commit em ${Date.now() - t0} ms`);
  print(`  ${membros.length} pessoas na rede`);
  print(`  ${contas.modifiedCount} contas marcadas`);
  print(`  ${pessoas.modifiedCount} pessoas marcadas`);
  print(`  caso ${caseId}`);
} catch (e) {
  session.abortTransaction();
  print(`abort: ${e}`);
} finally {
  session.endSession();
}
