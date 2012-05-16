"""MessageProperty -- a property storing ProtoRPC Message objects."""

from protorpc import messages
from protorpc import remote

from . import model
from . import utils

__all__ = ['MessageProperty']

protocols_registry = remote.Protocols.new_default()
default_protocol = 'protojson'  # While protobuf is faster, json is clearer.


class MessageProperty(model.StructuredProperty):

  _message_type = None
  _indexed_fields = ()
  _protocol_name = None
  _protocol_impl = None

  @utils.positional(3)
  def __init__(self, message_type, name=None, repeated=False,
               indexed_fields=None,
               protocol=None):
    if not (isinstance(message_type, type) and
            issubclass(message_type, messages.Message)):
      raise TypeError('MessageProperty argument must be a Message subclass')
    self._message_type = message_type
    if indexed_fields is not None:
      # TODO: Check they are all strings naming fields
      self._indexed_fields = tuple(indexed_fields)
    if protocol is None:
      protocol = default_protocol
    self._protocol_name = protocol
    self._protocol_impl = protocols_registry.lookup_by_name(protocol)
    class _MessageClass(model.Expando):
      blob_ = model.BlobProperty('__%s__' % self._protocol_name)
    for field_name in self._indexed_fields:
      try:
        message_type.field_by_name(field_name)
      except KeyError:
        raise ValueError('Message class %s does not have a field named %s' %
                         (message_type.__name__, field_name))
      field_prop = model.GenericProperty(field_name)
      setattr(_MessageClass, field_name, field_prop)
    _MessageClass._fix_up_properties()
    super(MessageProperty, self).__init__(_MessageClass, name,
                                          repeated=repeated)

  def _validate(self, msg):
    if not isinstance(msg, self._message_type):
      raise TypeError('Expected a %s instance for %s property',
                      self._message_type.__name__,
                      self._code_name or self._name)

  def _to_base_type(self, msg):
    ent = self._modelclass()
    ent.blob_ = self._protocol_impl.encode_message(msg)
    for field_name in self._indexed_fields:
      field_value = getattr(msg, field_name)
      setattr(ent, field_name, field_value)
    return ent

  def _from_base_type(self, ent):
    msg = self._protocol_impl.decode_message(self._message_type, ent.blob_)
    return msg
