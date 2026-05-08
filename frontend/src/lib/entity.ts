type ApiEntity = {
  id?: string | null;
  _id?: string | null;
};

export const getEntityId = (entity: ApiEntity): string =>
  String(entity.id ?? entity._id ?? '');

export const withEntityId = <T extends ApiEntity>(entity: T): T & { id: string } => ({
  ...entity,
  id: getEntityId(entity),
});
