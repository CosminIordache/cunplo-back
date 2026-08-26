from urllib.parse import quote

import aioboto3


class S3Storage:
  """Bucket compatible con S3 (el de Railway). Abre una sesión por operación:
  las subidas son esporádicas y así no hay cliente que mantener vivo entre jobs."""

  def __init__(self, endpoint_url: str, region: str, access_key: str, secret_key: str, bucket: str):
    self.bucket = bucket
    self.session = aioboto3.Session(
      aws_access_key_id=access_key,
      aws_secret_access_key=secret_key,
      region_name=region,
    )
    self.endpoint_url = endpoint_url

  def _client(self):
    return self.session.client("s3", endpoint_url=self.endpoint_url)

  async def put(self, key: str, data: bytes, content_type: str) -> str:
    async with self._client() as s3:
      await s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
    return key

  async def delete(self, keys: list[str]) -> None:
    if not keys:
      return
    async with self._client() as s3:
      # delete_objects acepta 1000 por llamada; de sobra para un usuario
      for chunk in (keys[i : i + 1000] for i in range(0, len(keys), 1000)):
        await s3.delete_objects(
          Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in chunk]}
        )

  async def signed_url(self, key: str, filename: str, expires_in: int = 3600) -> str:
    """URL temporal para que el navegador descargue directo, sin pasar por la API."""
    async with self._client() as s3:
      return await s3.generate_presigned_url(
        "get_object",
        Params={
          "Bucket": self.bucket,
          "Key": key,
          # sin esto el navegador abre el fichero con el nombre de la clave
          "ResponseContentDisposition": f'attachment; filename="{quote(filename)}"',
        },
        ExpiresIn=expires_in,
      )
