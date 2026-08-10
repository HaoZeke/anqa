//! Scroll rail with a usable minimum handle (ported from icedtea).

use iced::advanced::layout::{self, Layout};
use iced::advanced::renderer;
use iced::advanced::widget::tree::{self, Tree};
use iced::advanced::widget::Widget;
use iced::advanced::{Clipboard, Shell};
use iced::mouse;
use iced::{Background, Color, Element, Event, Length, Rectangle, Size};

use crate::live::{scroll_from_rail, scroller_span, SCROLL_HANDLE_MIN, SCROLL_RAIL_WIDTH};
use crate::theme::{mix, Tokens};

#[derive(Default)]
struct State {
    dragging: Option<f32>,
}

/// Vertical rail. `on_scroll` is the content offset (pixels).
pub struct ScrollRail<'a, Message> {
    content: f32,
    viewport: f32,
    scroll: f32,
    on_scroll: Box<dyn Fn(f32) -> Message + 'a>,
    tok: Tokens,
}

impl<'a, Message> ScrollRail<'a, Message> {
    pub fn new(
        content: f32,
        viewport: f32,
        scroll: f32,
        on_scroll: impl Fn(f32) -> Message + 'a,
        tok: Tokens,
    ) -> Self {
        Self {
            content,
            viewport,
            scroll,
            on_scroll: Box::new(on_scroll),
            tok,
        }
    }
}

fn thumb(content: f32, viewport: f32, scroll: f32, rail: f32) -> (f32, f32) {
    scroller_span(content, viewport, scroll, rail, SCROLL_HANDLE_MIN)
}

impl<Message, Theme, Renderer> Widget<Message, Theme, Renderer> for ScrollRail<'_, Message>
where
    Renderer: iced::advanced::Renderer,
{
    fn tag(&self) -> tree::Tag {
        tree::Tag::of::<State>()
    }

    fn state(&self) -> tree::State {
        tree::State::new(State::default())
    }

    fn size(&self) -> Size<Length> {
        Size::new(Length::Fixed(SCROLL_RAIL_WIDTH), Length::Fill)
    }

    fn layout(
        &self,
        _tree: &mut Tree,
        _renderer: &Renderer,
        limits: &layout::Limits,
    ) -> layout::Node {
        let size = limits.resolve(
            Length::Fixed(SCROLL_RAIL_WIDTH),
            Length::Fill,
            Size::new(SCROLL_RAIL_WIDTH, 0.0),
        );
        layout::Node::new(size)
    }

    fn on_event(
        &mut self,
        tree: &mut Tree,
        event: Event,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        _renderer: &Renderer,
        _clipboard: &mut dyn Clipboard,
        shell: &mut Shell<'_, Message>,
        _viewport: &Rectangle,
    ) -> iced::event::Status {
        let bounds = layout.bounds();
        let state = tree.state.downcast_mut::<State>();
        let rail = bounds.height;
        let (off, len) = thumb(self.content, self.viewport, self.scroll, rail);

        match event {
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)) => {
                let Some(pos) = cursor.position() else {
                    return iced::event::Status::Ignored;
                };
                if !bounds.contains(pos) {
                    return iced::event::Status::Ignored;
                }
                let y = pos.y - bounds.y;
                if y >= off && y <= off + len {
                    state.dragging = Some(y - off);
                } else {
                    let thumb_y = (y - len / 2.0).clamp(0.0, (rail - len).max(0.0));
                    state.dragging = Some(y - thumb_y);
                    shell.publish((self.on_scroll)(scroll_from_rail(
                        self.content,
                        self.viewport,
                        thumb_y,
                        rail,
                        SCROLL_HANDLE_MIN,
                    )));
                }
                iced::event::Status::Captured
            }
            Event::Mouse(mouse::Event::CursorMoved { .. }) => {
                let Some(grab) = state.dragging else {
                    return iced::event::Status::Ignored;
                };
                let Some(pos) = cursor.position() else {
                    return iced::event::Status::Ignored;
                };
                let y = pos.y - bounds.y;
                let thumb_y = (y - grab).clamp(0.0, (rail - len).max(0.0));
                shell.publish((self.on_scroll)(scroll_from_rail(
                    self.content,
                    self.viewport,
                    thumb_y,
                    rail,
                    SCROLL_HANDLE_MIN,
                )));
                iced::event::Status::Captured
            }
            Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)) => {
                if state.dragging.take().is_some() {
                    iced::event::Status::Captured
                } else {
                    iced::event::Status::Ignored
                }
            }
            Event::Mouse(mouse::Event::WheelScrolled { delta }) => {
                if !cursor.is_over(bounds) {
                    return iced::event::Status::Ignored;
                }
                let dy = match delta {
                    mouse::ScrollDelta::Lines { y, .. } => -y * 24.0,
                    mouse::ScrollDelta::Pixels { y, .. } => -y,
                };
                let max_scroll = (self.content - self.viewport).max(0.0);
                shell.publish((self.on_scroll)((self.scroll + dy).clamp(0.0, max_scroll)));
                iced::event::Status::Captured
            }
            _ => iced::event::Status::Ignored,
        }
    }

    fn mouse_interaction(
        &self,
        tree: &Tree,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        _viewport: &Rectangle,
        _renderer: &Renderer,
    ) -> mouse::Interaction {
        let state = tree.state.downcast_ref::<State>();
        if state.dragging.is_some() {
            return mouse::Interaction::Grabbing;
        }
        let bounds = layout.bounds();
        let Some(pos) = cursor.position() else {
            return mouse::Interaction::default();
        };
        if !bounds.contains(pos) {
            return mouse::Interaction::default();
        }
        let (off, len) = thumb(self.content, self.viewport, self.scroll, bounds.height);
        let y = pos.y - bounds.y;
        if y >= off && y <= off + len {
            mouse::Interaction::Grab
        } else {
            mouse::Interaction::Pointer
        }
    }

    fn draw(
        &self,
        _tree: &Tree,
        renderer: &mut Renderer,
        _theme: &Theme,
        _style: &renderer::Style,
        layout: Layout<'_>,
        _cursor: mouse::Cursor,
        _viewport: &Rectangle,
    ) {
        let bounds = layout.bounds();
        renderer.fill_quad(
            renderer::Quad {
                bounds,
                border: iced::Border {
                    color: Color::TRANSPARENT,
                    width: 0.0,
                    radius: 4.0.into(),
                },
                ..renderer::Quad::default()
            },
            Background::Color(self.tok.panel),
        );
        let (off, len) = thumb(self.content, self.viewport, self.scroll, bounds.height);
        if len <= 0.0 {
            return;
        }
        let thumb_bounds = Rectangle {
            x: bounds.x,
            y: bounds.y + off,
            width: bounds.width,
            height: len,
        };
        renderer.fill_quad(
            renderer::Quad {
                bounds: thumb_bounds,
                border: iced::Border {
                    color: Color::TRANSPARENT,
                    width: 0.0,
                    radius: 4.0.into(),
                },
                ..renderer::Quad::default()
            },
            Background::Color(mix(self.tok.text, self.tok.canvas, 0.35)),
        );
    }
}

impl<'a, Message: 'a> From<ScrollRail<'a, Message>> for Element<'a, Message> {
    fn from(value: ScrollRail<'a, Message>) -> Self {
        Self::new(value)
    }
}
