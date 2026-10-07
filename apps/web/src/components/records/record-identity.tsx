import { FingerprintFull } from "@/components/records/fingerprint";
import { Frame } from "@/components/ui/frame";
import { KeyValue, KeyValueList } from "@/components/ui/key-value";
import type { RecordDetail } from "@/lib/api/record-types";
import { formatUtcInstant } from "@/lib/format";
import { CREATED_AT_NOTE, FINGERPRINT_NOTE } from "@/lib/record-wording";

/**
 * Who this record is: its own identity, when it was saved, and the two fingerprints the record
 * detail supplies. They are different identities and are labelled as such: the manifest
 * fingerprint covers the request, methodology and consumed evidence; the result fingerprint covers
 * the stored result. Neither is the record's identity.
 *
 * The record is shown as the backend returned it after its own integrity validation. The browser
 * does not hash or verify anything, and nothing here claims it did.
 */
export function RecordIdentity({ record }: { readonly record: RecordDetail }) {
  return (
    <Frame id="section-identity" title="Stored baseline identity" note="Persisted metadata and fingerprints.">
      <KeyValueList>
        <KeyValue label="Record id">
          <FingerprintFull value={record.record_id} label="record id" />
          <div className="type-meta">The identity of this persisted record.</div>
        </KeyValue>
        <KeyValue label="Saved at (UTC)">
          <span className="num">{formatUtcInstant(record.created_at)}</span>
          <div className="type-meta">{CREATED_AT_NOTE}</div>
        </KeyValue>
        <KeyValue label="Manifest fingerprint">
          <FingerprintFull value={record.manifest_fingerprint} label="manifest fingerprint" />
          <div className="type-meta">{FINGERPRINT_NOTE.manifest}</div>
        </KeyValue>
        <KeyValue label="Result fingerprint">
          <FingerprintFull value={record.result_fingerprint} label="result fingerprint" />
          <div className="type-meta">{FINGERPRINT_NOTE.result}</div>
        </KeyValue>
        <KeyValue label="Hash algorithm">
          <span className="type-id">{record.hash_algorithm}</span>
          <div className="type-meta">The algorithm of both fingerprints. The backend re-validates the stored record when it serves it; this page does not hash anything.</div>
        </KeyValue>
      </KeyValueList>
    </Frame>
  );
}
