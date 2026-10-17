CREATE TRIGGER audiobook_annotation_immutable BEFORE UPDATE OR DELETE ON omnix_audiobook_annotations
    FOR EACH ROW EXECUTE FUNCTION omnix_audiobook_reject_immutable_change();
CREATE TRIGGER audiobook_casting_immutable BEFORE UPDATE OR DELETE ON omnix_audiobook_castings
    FOR EACH ROW EXECUTE FUNCTION omnix_audiobook_reject_immutable_change();
