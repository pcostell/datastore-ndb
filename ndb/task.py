"""A task decorator.

This should wrap a generator, e.g.:

  @task
  def foo():
    yield <some future>
    yield <another future>
    raise StopIteration(42)

Now calling foo() is equivalent to scheduler.schedule(foo()), and returns
a future that will produce 42 once foo() completes.

As a special feature, if foo() is not a generator, it will be run till
completion and the return value returned via a future.  This is to make
the following two equivalent:

  @task
  def foo():
    return 42

  @task
  def foo():
    if False: yield
    raise StopIteration(42)  # Or, after PEP 380, return 42

(This idea taken from Monocle.)

(How does Monocle do the trampoline?  Does it use the Twisted reactor?)
"""

import types

class Future(object):
  """A Future has 0 or more callbacks.

  The callbacks will be called when the result is ready.

  NOTE: This is somewhat inspired but not conformant to the Future interface
  defined by PEP 3148.  It is also inspired (and tries to be somewhat
  compatible with) the App Engine specific UserRPC and MultiRpc classes.
  """

  def __init__(self):
    self.done = False
    self.result = None
    self.exception = None
    self.callbacks = []

  def add(self, callback):
    if self.done:
      callback(self)
    else:
      self.callbacks.append(callback)

  def set_result(self, result):
    assert not self.done
    self.result = result
    self.done = True
    for callback in self.callbacks:
      callback(self)  # TODO: What if it raises an exception?

  def set_exception(self, exc):
    # TODO: What about tracebacks?
    assert isinstance(exc, BaseException)
    assert not self.done
    self.exception = exc
    self.done = True
    for callback in self.callbacks:
      callback(self)

  @property
  def state(self):
    # This is just for compatibility with UserRPC and MultiRpc.
    # A Future is considered running as soon as it is created.
    if self.done:
      return 2  # FINISHING
    else:
      return 1  # RUNNING

  def wait(self):
    assert self.done  # TODO: How to wait until set_*() is called?

  def check_success(self):
    self.wait()
    if self.exception is not None:
      raise self.exception

  def get_result(self):
    self.check_success()
    return self.result

  @classmethod
  def wait_any(cls, futures):
    all = set(futures)
    for f in all:
      if f.state == 2:
        return f
    assert False, 'XXX what to do now?'

  @classmethod
  def wait_all(cls, futures):
    todo = set(futures)
    while todo:
      f = cls.wait_any(todo)
      if f is not None:
        todo.remove(f)

class Return(StopIteration):
  """Trivial StopIteration class, used to mark return values.

  To use this, raise Return(<your return value>).  The semantics
  are exactly the same as raise StopIteration(<your return value>)
  but using Return clarifies that you are intending this to be the
  return value of a coroutine.
  """

def task(func):
  def wrapper(*args, **kwds):
    try:
      result = func(*args, **kwds)
      if isinstance(result, types.GeneratorType):
        XXX
    except Exception:
      # NOTE: Don't catch BaseException or string exceptions or
      # exceptions not deriving from Exception.  BaseException
      # deserves to quit the whole program, and string exceptions
      # shouldn't be used at all (they're deprecated in Python 2.5 and
      # cannot be raised in Python 2.6).  Exceptions not deriving from
      # BaseException are theoretically still allowed, but they are
      # not recommended.
      XXX

# XXX Where to put the trampoline code?  Yielding a generator ought to
# schedule that generator -- I've got code in the old NDB to do that.
# The important lesson from Monocle is to separate the trampoline code
# from the event loop.  In particular when PEP 380 goes live (alas,
# not before Python 3.3), the trampoline will no longer be necessary,
# but it has no bearing on the event loop.  I hope.

# XXX A task/coroutine/generator can yield the following things:
# - Another task/coroutine/generator; this is entirely equivalent to
#   "for x in g: yield x"; this is handled entirely by the @task wrapper.
# - An RPC (or MultiRpc); it will be resumed when this completes;
#   this does not use the RPC's callback mechanism.
# - A Future; it will be resumed when the Future is done.
#   This adds a callback to the Future which does roughly:
#     def cb(f):
#       assert f.done
#       if f.exception is not None:
#         g.throw(f)
#       else:
#         g.send(f.result)
#   But when this raises an exception, that exception ought to be
#   propagated back into whatever started *this* generator.

# XXX A Future can be used in several ways:
# - Yield it from a task/coroutine/generator; see above.
# - Check (poll) its status via f.done, f.exception, f.result.
# - Call its wait() method, perhaps indirectly via check_success()
#   or get_result().  What does this do?  Invoke the event loop.
# - Call the Future.wait_any() or Future.wait_all() method.
#   This is supposed to wait for all Futures and RPCs in the argument
#   list.  How does it do this?  By invoking the event loop.
#   (Should we bother?)

# XXX I still can't quite get my head around the intricacies of the
# interaction between coroutines, futures, and the event loop, even
# though I've implemented several of these before...  Maybe a starting
# point would be to implement something that transparently does
# *nothing* except converting "yield a generator g" into (almost just)
# "for x in g: yield x" in such a way that this code can simply be
# deleted once PEP 380 exists (and the code should be changed from
# "yield g" to "yield from g").  A tricky detail may be to make it
# easy to debug problems -- tracebacks involving generators generally
# stink.

def gwrap(func):
  """Decorator to emulate PEP 380 behavior.

  Inside a generator function wrapped in @gwrap, 'yield g', where g is
  a generator object, should be equivalent to 'for x in g: yield x',
  except that 'yield g' can also return a value, and that value is
  whatever g passed as the argument to StopIteration when it stopped.

  NOTE: This is not quite the same as @task, which offers event loop
  integration.
  """
  def gwrap_wrapper(*args, **kwds):
    g = func(*args, **kwds)  # If it fails in this stage, so be it.
    if not isinstance(g, types.GeneratorType):
      # If it doesn't return a generator object, return that.
      # TODO: Does this deserve a warning?
      raise Return(g)
    # The following is several elaborations on "for x in g: yield x".
    # The first elaboration is to pass values or exceptions received
    # from yield back into g.  That's just part of a truly transparent
    # wrapper for a generator.  The second elaboration is to enter
    # a recursive loop when x is a generator.  That's part of emulating
    # PEP 380 so that "yield g" is interpreted as "yield from g".
    # The third elaboration is to pass values and exceptions up that
    # chain.  This is where my brain keeps hurting.
    to_send = None
    to_throw = None
    stack = [g]
    while stack:
      g = stack[-1]
      try:
        if to_throw is not None:
          g.throw(to_throw)
        else:
          to_yield = g.send(to_send)
      except StopIteration, err:
        stack.pop()
        if not stack:
          raise
        to_send = None
        if err.args:
          if len(err.args) == 1:
            to_send = err.args[0]
          else:
            to_send = err.args
        to_throw = None
        continue
      except Exception, err:
        stack.pop()
        if not stack:
          raise
        to_send = None
        to_throw = err
        continue
      else:
        to_throw = None
        to_send = None
        if not isinstance(to_yield, types.GeneratorType):
          try:
            to_send = yield to_yield
          except Exception, err:
            to_throw = err
        else:
          stack.append(to_yield)

  return gwrap_wrapper
