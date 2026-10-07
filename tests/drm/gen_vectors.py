"""Ed25519/SHA-512 Lua vs Python cross-check: python tests/drm/gen_vectors.py && luajit tests/drm/run_vectors.lua"""
import os, hashlib, json, random
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization as s
random.seed(1); V=[]
for i in range(12):
    k=Ed25519PrivateKey.generate()
    pk=k.public_key().public_bytes(s.Encoding.Raw,s.PublicFormat.Raw).hex()
    msg=os.urandom(random.choice([0,1,55,111,112,127,128,129,200,300])).hex()
    m=bytes.fromhex(msg); sig=k.sign(m).hex()
    V.append(dict(pk=pk,msg=msg,sig=sig,ok=True,sha=hashlib.sha512(m).hexdigest()))
    bad=bytearray(bytes.fromhex(sig)); bad[i%64]^=1
    V.append(dict(pk=pk,msg=msg,sig=bad.hex(),ok=False,sha=hashlib.sha512(m).hexdigest()))
    if m: 
        mm=bytearray(m); mm[0]^=0x80
        V.append(dict(pk=pk,msg=mm.hex(),sig=sig,ok=False,sha=hashlib.sha512(bytes(mm)).hexdigest()))
    other=Ed25519PrivateKey.generate().public_key().public_bytes(s.Encoding.Raw,s.PublicFormat.Raw).hex()
    V.append(dict(pk=other,msg=msg,sig=sig,ok=False,sha=hashlib.sha512(m).hexdigest()))
# malleability: S + L
L=2**252+27742317777372353535851937790883648493
v=V[0]; sb=bytes.fromhex(v['sig']); S=int.from_bytes(sb[32:],'little')+L
if S<2**256: V.append(dict(pk=v['pk'],msg=v['msg'],sig=(sb[:32]+S.to_bytes(32,'little')).hex(),ok=False,sha=v['sha']))
with open(__import__('os').path.join(__import__('os').path.dirname(__file__),'vectors.lua'),'w') as f:
    f.write("return {\n"+",\n".join("{pk='%s',msg='%s',sig='%s',ok=%s,sha='%s'}"%(x['pk'],x['msg'],x['sig'],str(x['ok']).lower(),x['sha']) for x in V)+"\n}\n")
print(len(V),"vectors")
