from xml.etree.ElementTree import TreeBuilder, XML, tostring
from urllib2 import urlopen, HTTPPasswordMgr, HTTPBasicAuthHandler, install_opener, build_opener


FORCE_DECLARED = "FORCE_DECLARED"
"""
The projection handling policy for layers that should use coordinates
directly while reporting the configured projection to clients.  This should be
used when projection information is missing from the underlying datastore.
"""

FORCE_NATIVE = "FORCE_NATIVE"
"""
The projection handling policy for layers that should use the projection
information from the underlying storage mechanism directly, and ignore the
projection setting.
"""

REPROJECT = "REPROJECT"
"""
The projection handling policy for layers that should use the projection
information from the underlying storage mechanism to reproject to the
configured projection.
"""


class ResourceInfo(object):
  """A base class for all resource types managed by the catalog """

  resource_type = 'abstractResourceType'
  """A string identifier for the *type* of resource, such as layer or style"""

  def update(self):
    self.metadata = get_xml(self.href)
    self.name = self.metadata.find('name').text

  def serialize(self):
    builder = TreeBuilder()
    builder.start(self.resource_type, dict())
    self.encode(builder)
    builder.end(self.resource_type)
    return tostring(builder.close())

  def encode(self, builder):
    """
    Add appropriate XML nodes to this object.  The builder will be passed in
    ready to go, with the appropriate top-level node already added.
    """
    pass


def get_xml(url):
  password_manager = HTTPPasswordMgr()
  password_manager.add_password(
    realm='GeoServer Realm',
    uri='http://localhost:8080/geoserver/',
    user='admin',
    passwd='geoserver'
  )

  handler = HTTPBasicAuthHandler(password_manager)
  install_opener(build_opener(handler))
  
  response = urlopen(url).read()
  try:
      return XML(response)
  except:
      print "%s => \n%s" % (url, response)

def atom_link(node):
    return node.find("{http://www.w3.org/2005/Atom}link").get("href")

def atom_link_xml(builder, href):
    builder.start("atom:link", {
        'rel': 'alternate',
        'href': href,
        'type': 'application/xml',
        'xmlns:atom': 'http://www.w3.org/2005/Atom'
    })
    builder.end("atom:link")

def bbox(node):
    minx = node.find("minx")
    maxx = node.find("maxx")
    miny = node.find("miny")
    maxy = node.find("maxy")
    crs  = node.find("crs")
    if (None not in [minx, maxx, miny, maxy, crs]):
        return (minx.text, maxx.text, miny.text, maxy.text, crs.text)
    else:
        return None

def bbox_xml(builder, bbox):
    minx, maxx, miny, maxy, crs = bbox
    builder.start("minx", dict())
    builder.data(minx)
    builder.end("minx")
    builder.start("maxx", dict())
    builder.data(maxx)
    builder.end("maxx")
    builder.start("miny", dict())
    builder.data(miny)
    builder.end("miny")
    builder.start("maxy", dict())
    builder.data(maxy)
    builder.end("maxy")
    builder.start("crs", {"class": "projected"})
    builder.data(crs)
    builder.end("crs")

